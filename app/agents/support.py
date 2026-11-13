"""Agent 间共享的小工具：上下文构建、Prompt 分段、请求转换。

外部工具 / RAG 结果进入 Prompt 前在此统一裁剪长度与字段，视为不可信输入。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from app.agent.nodes import _one_way_routes
from app.agents.schemas import (
    KnowledgeResult,
    RealtimeResult,
    RequirementResult,
)
from app.schemas import PlanRequest, RouteResult, TicketInfo
from app.services.itinerary import Candidate, ItineraryContext
from app.services.relevance import relevance_score

# 进入 Prompt 的字段长度上限（外部数据不可信，需裁剪）
_MAX_TEXT = 800
_MAX_LINES = 60


def build_context(req: RequirementResult, state: dict, deps: Any) -> ItineraryContext:
    return ItineraryContext(
        departure=req.departure or (state.get("departure") or ""),
        travel_date=req.travel_date or state.get("travel_date") or date.today(),
        people_count=req.people_count or state.get("people_count") or 1,
        budget=req.budget if req.budget is not None else Decimal("500"),
        interests=req.interests,
        has_elderly=bool(req.has_elderly),
        has_children=bool(req.has_children),
        latest_return_time=req.latest_return_time or "21:00",
        weather=state.get("weather"),
        amap=deps.amap,
        lunch_minutes=deps.settings.lunch_minutes,
        buffer_ratio=deps.settings.time_buffer_ratio,
        max_one_way_drive_minutes=deps.settings.max_one_way_drive_minutes,
        start_time=req.start_time or "08:00",
    )


def tickets_from_realtime(realtime: RealtimeResult | None) -> dict[str, TicketInfo]:
    if realtime is None:
        return {}
    return dict(realtime.ticket_map)


def routes_from_realtime(realtime: RealtimeResult | None) -> dict[str, RouteResult]:
    if realtime is None:
        return {}
    return dict(realtime.route_map)


def one_way_routes(state: dict, deps: Any, ctx: ItineraryContext, candidates: list) -> dict[str, RouteResult]:
    return _one_way_routes(state, deps, ctx, candidates)


def request_from_requirement(req: RequirementResult) -> PlanRequest:
    return PlanRequest(
        departure=req.departure or "",
        travel_date=req.travel_date or date.today(),
        people_count=req.people_count or 1,
        budget=req.budget if req.budget is not None else Decimal("1"),
        interests=req.interests,
        transport_mode="driving",
        physical_level=req.physical_level or 3,
        has_elderly=bool(req.has_elderly),
        has_children=bool(req.has_children),
        latest_return_time=req.latest_return_time or "21:00",
        start_time=req.start_time or "08:00",
        exclude_attractions=req.exclude_attractions,
        exclude_districts=req.exclude_districts,
        max_one_way_drive_km=req.max_one_way_drive_km,
    )


def user_section(req: RequirementResult) -> str:
    lines = [
        f"- 出发地：{req.departure or '未指定'}",
        f"- 日期：{req.travel_date or '未指定'}",
        f"- 出发时间：{req.start_time or '08:00'}",
        f"- 出行人数：{req.people_count or '未指定'} 人"
        + ("（含老人，需低强度）" if req.has_elderly else "")
        + ("（含儿童）" if req.has_children else ""),
        f"- 预算：{req.budget or '未指定'} 元",
        f"- 兴趣：{'、'.join(req.interests) or '未指定'}",
        f"- 最晚返程：{req.latest_return_time or '未指定'}",
    ]
    if req.exclude_attractions:
        lines.append(f"- 排除景点：{'、'.join(req.exclude_attractions)}")
    if req.exclude_districts:
        lines.append(f"- 排除区县：{'、'.join(req.exclude_districts)}")
    if req.include_districts:
        lines.append(f"- 倾向于：{'、'.join(req.include_districts)}")
    if req.max_one_way_drive_km:
        lines.append(f"- 单程驾车不超过 {req.max_one_way_drive_km} 公里")
    return "\n".join(lines)


def knowledge_section(knowledge: KnowledgeResult | None) -> str:
    if knowledge is None or not knowledge.documents:
        return ""
    parts: list[str] = []
    for d in knowledge.documents:
        name = d.attraction or f"条目{d.doc_id or '?'}"
        tag = f"（{d.district}）" if d.district else ""
        src = d.source_url or "无来源链接"
        text = _cut(d.text)
        parts.append(f"### {name}{tag}\n{text}\n- 来源：{src}；doc_id：{d.doc_id}")
    return "\n\n".join(parts)


def realtime_section(realtime: RealtimeResult | None, candidates: list) -> str:
    lines: list[str] = []
    if realtime is None:
        return ""
    w = realtime.weather
    if w is not None:
        tmin, tmax = w.temp_min, w.temp_max
        temp = f"{tmin}~{tmax}°C" if (tmin is not None and tmax is not None) else "未知"
        text = w.text_day or "未知"
        rain = "；请注意降雨，优先室内低强度景点" if (w.precip or 0) > 0 else ""
        lines.append(f"- 天气：{text}，气温 {temp}{rain}")
    for c in candidates:
        a = c.attraction
        r = realtime.route_map.get(a.name)
        drive = f"{r.distance_km}公里/{r.duration_minutes}分钟" if r else "路线未知"
        tk = realtime.ticket_map.get(a.name)
        if tk is not None and tk.price is not None:
            fee = f"{tk.price} 元"
        elif a.ticket_price is not None:
            fee = f"{a.ticket_price} 元"
        else:
            fee = a.ticket_note or "暂无可靠票价数据"
        lines.append(f"- {a.name}（{a.district}）：单程驾车约 {drive}；门票 {fee}")
    return "\n".join(lines[: _MAX_LINES])


def proposal_summary(knowledge: KnowledgeResult | None) -> str:
    if knowledge is None:
        return ""
    names = [d.attraction or "" for d in knowledge.documents if d.attraction]
    unique: list[str] = []
    for n in names:
        if n not in unique:
            unique.append(n)
    return "候选相关条目：" + "、".join(unique)


def _cut(text: str) -> str:
    text = (text or "").strip()
    return text if len(text) <= _MAX_TEXT else text[:_MAX_TEXT] + "…"


def _match_score(attr: Any, interests: list[str]) -> float:
    return relevance_score(attr, interests)


def build_ranked_candidates(
    attractions: list[Any],
    interests: list[str],
    has_elderly: bool = False,
    raining: bool = False,
) -> list[Candidate]:
    """确定性给候选景点排序并包装为 Candidate（去重、按兴趣得分降序）。

    指定兴趣时剔除零相关景点；否则「轻松备选」会挑到完全不相关的景点。
    """
    ranked: list[Candidate] = []
    seen: set[str] = set()
    for attr in attractions:
        key = (attr.name.strip().lower(), attr.district)
        if key in seen:
            continue
        seen.add(key)
        score = _match_score(attr, interests)
        if interests and score <= 0:
            continue
        if raining and attr.indoor_ratio < 0.4:
            score -= 2
        if has_elderly and attr.physical_level > 2:
            score -= 3
        ranked.append(Candidate(attraction=attr, score=score))
    ranked.sort(key=lambda c: c.score, reverse=True)
    return ranked
