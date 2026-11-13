"""LangGraph 节点实现。

所有确定性计算（距离/时间/预算/约束）在 Python 内完成；LLM 仅用于
理解需求、生成推荐理由与解释，且可整体关闭（deps.llm=None）。
"""
from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal

from app.schemas import PlanRequest, PlanResponse
from app.services.itinerary import (
    Candidate,
    ItineraryContext,
    _build_option,
    _coord,
    _departure_coord,
    _route,
    build_options,
)
from app.tools.amap import POISearchInput, mock_coord_for_district
from app.tools.attraction_search import AttractionSearchInput, search_attractions
from app.tools.tickets import TicketQuery
from app.tools.weather import WeatherError, WeatherQuery
from app.utils.redact import redact


def parse_request(state: dict, deps) -> dict:
    req = (state.get("user_request") or "").strip()
    parsed = None
    if req and deps.llm and deps.llm.enabled and _missing_structured(state):
        parsed = deps.llm.parse_request(req)

    out = {
        "transport_mode": state.get("transport_mode") or "driving",
        "physical_level": state.get("physical_level") or 3,
        "has_elderly": bool(state.get("has_elderly")),
        "has_children": bool(state.get("has_children")),
        "latest_return_time": state.get("latest_return_time") or deps.settings.default_latest_return,
        "start_time": state.get("start_time") or "08:00",
        "interests": list(state.get("interests") or []),
        "max_revision_iterations": deps.settings.max_revision_iterations,
        "iteration_count": 0,
        "itinerary_max_attractions": 3,
        "exclude_attractions": list(state.get("exclude_attractions") or []),
        "exclude_districts": list(state.get("exclude_districts") or []),
        "max_one_way_drive_km": state.get("max_one_way_drive_km"),
    }
    out["departure"] = state.get("departure") or (parsed.departure if parsed else None)
    out["travel_date"] = state.get("travel_date") or (parsed.travel_date if parsed else None)
    out["people_count"] = state.get("people_count") or (parsed.people_count if parsed else None)
    out["budget"] = (
        state.get("budget") if state.get("budget") is not None else (parsed.budget if parsed else None)
    )
    if parsed and parsed.interests and not out["interests"]:
        out["interests"] = parsed.interests
    if parsed:
        out["physical_level"] = parsed.physical_level or out["physical_level"]
        out["has_elderly"] = out["has_elderly"] or parsed.has_elderly
        out["has_children"] = out["has_children"] or parsed.has_children
        if parsed.latest_return_time:
            out["latest_return_time"] = parsed.latest_return_time
    return out


def check_required_fields(state: dict, deps) -> dict:
    missing: list[str] = []
    if not state.get("departure"):
        missing.append("出发位置")
    if state.get("travel_date") is None:
        missing.append("出行日期")
    if not state.get("people_count"):
        missing.append("出行人数")
    if state.get("budget") is None:
        missing.append("预算")

    if missing:
        return {
            "missing_fields": missing,
            "clarification_required": True,
            "clarification_question": "请补充：" + "、".join(missing),
        }
    return {"missing_fields": [], "clarification_required": False, "clarification_question": ""}


def retrieve_candidates(state: dict, deps) -> dict:
    interests = state.get("interests", [])
    season = _season_of(state.get("travel_date"))
    inp = AttractionSearchInput(
        interests=interests,
        max_physical_level=2 if state.get("has_elderly") else state.get("physical_level"),
        require_elderly_friendly=bool(state.get("has_elderly")),
        require_children_friendly=bool(state.get("has_children")),
        season=season,
        top_k=6,
    )
    attractions = search_attractions(inp)
    if not attractions and season:
        attractions = search_attractions(
            AttractionSearchInput(
                interests=interests,
                max_physical_level=2 if state.get("has_elderly") else state.get("physical_level"),
                require_elderly_friendly=bool(state.get("has_elderly")),
                require_children_friendly=bool(state.get("has_children")),
                top_k=6,
            )
        )
    attractions = _apply_exclusions(attractions, state)

    base_query = " ".join(interests) or "石家庄 一日游"
    retrieval_filters: dict = {}
    if deps.llm and deps.llm.enabled:
        rq = deps.llm.generate_retrieval_query(state.get("user_request", ""), interests)
        if rq:
            if rq.get("query"):
                base_query = rq["query"]
            if rq.get("district"):
                retrieval_filters["district"] = rq["district"] or ""
            if rq.get("category"):
                retrieval_filters["category"] = rq["category"] or ""

    docs = _safe_retrieve(deps, base_query, filters=retrieval_filters)
    return {
        "candidate_attractions": attractions,
        "retrieved_documents": [{"text": d.text, "metadata": d.metadata} for d in docs],
        "retrieval_query": base_query,
    }


def fetch_weather(state: dict, deps) -> dict:
    # 和风天气新版专属 API Host 不接受中文城市名，需使用 LocationID（石家庄=101090101）
    query = WeatherQuery(location="101090101", date=state["travel_date"], mode="live")
    result = deps.weather.get(query)
    if isinstance(result, WeatherError):
        return {"weather": None, "weather_error": result}
    return {"weather": result, "weather_error": None}


def fetch_live_information(state: dict, deps) -> dict:
    enriched: list = []
    ticket_results: list = []
    for attr in state.get("candidate_attractions", []):
        poi = deps.amap.search_poi(POISearchInput(name=attr.name))
        if poi is not None and poi.longitude is not None and poi.latitude is not None:
            attr.longitude = poi.longitude
            attr.latitude = poi.latitude
        else:
            lng, lat = mock_coord_for_district(attr.district)
            attr.longitude = lng
            attr.latitude = lat
        ticket = deps.tickets.get_ticket_info(TicketQuery(attraction=attr.name, district=attr.district))
        attr.ticket_price = ticket.price
        attr.official_ticket_url = ticket.official_url
        ticket_results.append(ticket)
        enriched.append(attr)
    return {"candidate_attractions": enriched, "ticket_results": ticket_results}


def rank_candidates(state: dict, deps) -> dict:
    raining = _raining(state.get("weather"))
    interests = state.get("interests", [])
    ranked: list[Candidate] = []
    seen: set = set()
    for attr in state.get("candidate_attractions", []):
        # 信息去重：同区县且名称相似或同名景点只保留一次
        key = (attr.name.strip().lower(), attr.district)
        if key in seen:
            continue
        seen.add(key)
        score = _score(attr, interests, raining, bool(state.get("has_elderly")))
        reason = deps.llm.rank_reason(attr.name, interests) if deps.llm else ""
        ranked.append(Candidate(attraction=attr, score=score, reason=reason))
    ranked.sort(key=lambda c: c.score, reverse=True)
    return {"ranked_candidates": ranked}


def build_itinerary(state: dict, deps) -> dict:
    ctx, candidates, tickets = _plan_params(state, deps)
    depth = state.get("itinerary_max_attractions", 3)
    options, routes = _build_via_llm(
        state, deps, ctx, candidates, tickets,
        max_attractions=depth, feedback=state.get("validation_feedback", ""),
    )
    return {"itinerary_options": options, "route_results": routes}


def _plan_params(state: dict, deps) -> tuple:
    ctx = ItineraryContext(
        departure=state["departure"],
        travel_date=state["travel_date"],
        people_count=state["people_count"],
        budget=state["budget"],
        interests=state.get("interests", []),
        has_elderly=bool(state.get("has_elderly")),
        has_children=bool(state.get("has_children")),
        latest_return_time=state["latest_return_time"],
        start_time=state.get("start_time") or "08:00",
        weather=state.get("weather"),
        amap=deps.amap,
        lunch_minutes=deps.settings.lunch_minutes,
        buffer_ratio=deps.settings.time_buffer_ratio,
        max_one_way_drive_minutes=deps.settings.max_one_way_drive_minutes,
    )
    candidates: list[Candidate] = state.get("ranked_candidates", [])
    tickets = {r.attraction: r for r in state.get("ticket_results", [])}
    return ctx, candidates, tickets


def _build_via_llm(
    state: dict, deps, ctx, candidates: list, tickets: dict, max_attractions: int, feedback: str = ""
) -> tuple:
    """LLM 依「知识库+实时工具」生成结构化行程；失败/关闭时走确定性兜底。

    数值字段（路线距离/时间轴/预算）仍由 Python 确定性计算，LLM 只决定选点与顺序。
    """
    llm = deps.llm
    if llm and llm.enabled and candidates:
        try:
            one_way = _one_way_routes(state, deps, ctx, candidates)
            plan = llm.plan_itinerary(
                _user_section(state),
                _knowledge_section(state),
                _realtime_section(state, candidates, tickets, one_way),
                feedback,
            )
            if plan and plan.get("options"):
                by_name = {c.attraction.name: c for c in candidates}
                options: list = []
                routes: list = []
                for opt in plan["options"]:
                    chosen: list[Candidate] = []
                    for item in opt.get("attractions") or []:
                        cand = by_name.get(item["name"])
                        if cand is not None:
                            cand.attraction.suggested_duration_minutes = item.get(
                                "suggested_duration_minutes", cand.attraction.suggested_duration_minutes
                            )
                            chosen.append(cand)
                    if not chosen:
                        continue
                    built, built_routes = _build_option(opt["title"], chosen, ctx, tickets, candidates)
                    if opt.get("reason"):
                        built.reason = opt["reason"]
                    else:
                        built.reason = "；".join(c.reason for c in chosen if c.reason) or built.reason
                    options.append(built)
                    routes.extend(built_routes)
                if options:
                    return options, routes
        except Exception as exc:  # noqa: BLE001 - 任何故障都降级到确定性生成
            _logger.warning("LLM 生成行程失败，走确定性兜底：%s", redact(exc))
    return build_options(candidates, ctx, tickets, max_attractions=max_attractions)


def _user_section(state: dict) -> str:
    lines = [
        f"- 出发地：{state.get('departure') or '未指定'}",
        f"- 日期：{state.get('travel_date') or '未指定'}",
        f"- 出发时间：{state.get('start_time') or '08:00'}",
        f"- 出行人数：{state.get('people_count') or '未指定'} 人"
        + ("（含老人，需低强度）" if state.get("has_elderly") else "")
        + ("（含儿童）" if state.get("has_children") else ""),
        f"- 预算：{state.get('budget') or '未指定'} 元",
        f"- 兴趣：{'、'.join(state.get('interests') or []) or '未指定'}",
        f"- 最晚返程：{state.get('latest_return_time') or '未指定'}",
    ]
    if state.get("exclude_attractions"):
        lines.append(f"- 排除景点：{'、'.join(state['exclude_attractions'])}")
    if state.get("exclude_districts"):
        lines.append(f"- 排除区县：{'、'.join(state['exclude_districts'])}")
    if state.get("max_one_way_drive_km"):
        lines.append(f"- 单程驾车不超过 {state['max_one_way_drive_km']} 公里")
    return "\n".join(lines)


def _knowledge_section(state: dict) -> str:
    parts: list[str] = []
    for i, d in enumerate(state.get("retrieved_documents", [])):
        meta = d.get("metadata", {})
        name = meta.get("attraction") or f"条目{i + 1}"
        tag = f"（{meta.get('district')}）" if meta.get("district") else ""
        if meta.get("ticket_price") is not None:
            ticket = f"{meta['ticket_price']} 元"
        else:
            ticket = meta.get("ticket_note") or "暂无可靠票价数据"
        src = meta.get("source_url") or "无来源链接"
        text = (d.get("text") or "").strip()
        parts.append(f"### {name}{tag}\n{text}\n- 来源：{src}；门票参考：{ticket}")
    return "\n\n".join(parts)


def _realtime_section(state: dict, candidates: list, tickets: dict, one_way: dict) -> str:
    lines: list[str] = []
    w = state.get("weather")
    if w is not None:
        tmin = getattr(w, "temp_min", None)
        tmax = getattr(w, "temp_max", None)
        temp = f"{tmin}~{tmax}°C" if (tmin is not None and tmax is not None) else "未知"
        text = getattr(w, "text_day", None) or "未知"
        rain = "；请注意降雨，优先室内低强度景点" if _raining(w) else ""
        lines.append(f"- 天气：{text}，气温 {temp}{rain}")
    for c in candidates:
        a = c.attraction
        r = one_way.get(a.name)
        drive = f"{r.distance_km}公里/{r.duration_minutes}分钟" if r else "路线未知"
        tk = tickets.get(a.name)
        if tk is not None and tk.price is not None:
            fee = f"{tk.price} 元"
        elif a.ticket_price is not None:
            fee = f"{a.ticket_price} 元"
        else:
            fee = a.ticket_note or "暂无可靠票价数据"
        lines.append(f"- {a.name}（{a.district}）：单程驾车约 {drive}；门票 {fee}")
    return "\n".join(lines)


def _one_way_routes(state: dict, deps, ctx, candidates: list) -> dict:
    dep_lng, dep_lat = _departure_coord(ctx.departure)
    routes: dict = {}
    for c in candidates:
        a = c.attraction
        lng, lat = _coord(a)
        r = _route(ctx, ctx.departure, a.name, dep_lng, dep_lat, lng, lat)
        routes.setdefault(a.name, r)
    return routes


def validate_itinerary(state: dict, deps) -> dict:
    request = _request_from_state(state)
    all_issues: list = []
    for opt in state.get("itinerary_options", []):
        all_issues.extend(deps.validator.validate(opt, request))
    return {
        "validation_errors": [i for i in all_issues if i.blocking],
        "validation_risks": [i for i in all_issues if not i.blocking],
    }


def revise_itinerary(state: dict, deps) -> dict:
    iteration = state.get("iteration_count", 0) + 1
    errors = state.get("validation_errors", [])
    feedback = "\n".join(f"- {e.message}" for e in errors) if errors else ""
    ctx, candidates, tickets = _plan_params(state, deps)
    max_att = max(1, state.get("itinerary_max_attractions", 3) - 1)
    options, routes = _build_via_llm(
        state, deps, ctx, candidates, tickets, max_attractions=max_att, feedback=feedback
    )
    return {
        "itinerary_options": options,
        "route_results": routes,
        "iteration_count": iteration,
        "itinerary_max_attractions": max_att,
    }


def generate_response(state: dict, deps) -> dict:
    if state.get("clarification_required"):
        return {
            "response": PlanResponse(
                clarification_required=True,
                missing_fields=state.get("missing_fields", []),
                clarification_question=state.get("clarification_question"),
            )
        }

    citations = _build_citations(state)
    risks = [i.message for i in state.get("validation_risks", [])]
    options = state.get("itinerary_options", [])
    for opt in options:
        opt.risks = list(opt.risks) + risks
        opt.citations = citations

    return {
        "response": PlanResponse(itinerary_options=options),
        "citations": citations,
    }


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_logger = logging.getLogger(__name__)


def _safe_retrieve(deps, query: str, top_k: int = 5, filters: dict | None = None) -> list:
    """检索知识库；向量库未就绪/迁移时降级为空结果，不阻塞行程生成。"""
    try:
        return deps.retriever.retrieve(query, filters=filters, top_k=top_k)
    except Exception as exc:  # noqa: BLE001 - RAG 为可选项，任何故障都降级
        _logger.warning("知识库检索失败，降级为空：%s", redact(exc))
        return []


def _apply_exclusions(attractions: list, state: dict) -> list:
    """按用户「不去某景点/某区县」约束过滤候选景点。"""
    exclude_names = {n.strip() for n in state.get("exclude_attractions", []) if n}
    exclude_districts = {d.strip() for d in state.get("exclude_districts", []) if d}
    if not exclude_names and not exclude_districts:
        return attractions
    return [
        a
        for a in attractions
        if a.name not in exclude_names and a.district not in exclude_districts
    ]


def _missing_structured(state: dict) -> bool:
    return not all(
        [
            state.get("departure"),
            state.get("travel_date") is not None,
            state.get("people_count"),
            state.get("budget") is not None,
        ]
    )


def _season_of(d: date | None) -> str | None:
    if d is None:
        return None
    m = d.month
    if m in (3, 4, 5):
        return "春季"
    if m in (6, 7, 8):
        return "夏季"
    if m in (9, 10, 11):
        return "秋季"
    return "冬季"


def _raining(weather) -> bool:
    if weather is None:
        return False
    if getattr(weather, "precip", None) is not None and weather.precip > 0:
        return True
    return bool(getattr(weather, "text_day", None) and "雨" in weather.text_day)


def _score(attr, interests: list[str], raining: bool, has_elderly: bool) -> float:
    hay = (
        f"{attr.name} {attr.district} {attr.introduction} "
        f"{' '.join(attr.categories)} {' '.join(attr.tags)} {' '.join(attr.suitable_seasons)}"
    ).lower()
    score = 0.0
    for q in interests:
        ql = q.lower()
        if ql in attr.name.lower():
            score += 5
        elif ql in hay:
            score += 2
    if raining:
        score += 2 if attr.indoor_ratio >= 0.4 else -4
    if has_elderly and attr.physical_level > 2:
        score -= 3
    return score


def _request_from_state(state: dict) -> PlanRequest:
    return PlanRequest(
        departure=state.get("departure") or "",
        travel_date=state.get("travel_date") or date.today(),
        people_count=state.get("people_count") or 1,
        budget=state.get("budget") if state.get("budget") is not None else Decimal("1"),
        interests=state.get("interests", []),
        transport_mode="driving",
        physical_level=state.get("physical_level") or 3,
        has_elderly=bool(state.get("has_elderly")),
        has_children=bool(state.get("has_children")),
        latest_return_time=state.get("latest_return_time") or "21:00",
        start_time=state.get("start_time") or "08:00",
        exclude_attractions=state.get("exclude_attractions", []),
        exclude_districts=state.get("exclude_districts", []),
        max_one_way_drive_km=state.get("max_one_way_drive_km"),
    )


def _build_citations(state: dict) -> list[str]:
    out: list[str] = []
    w = state.get("weather")
    if w is not None:
        t = w.updated_at.strftime("%Y-%m-%d %H:%M") if w.updated_at else "未知"
        out.append(f"天气：{w.source}，更新于 {t}")
    err = state.get("weather_error")
    if err is not None:
        out.append(f"天气：{err.source} 查询失败（{err.code}：{err.message}）")

    seen_route: set = set()
    for r in state.get("route_results", []):
        t = r.queried_at.strftime("%Y-%m-%d %H:%M") if r.queried_at else "未知"
        key = (r.source, t)
        if key not in seen_route:
            seen_route.add(key)
            out.append(f"路线：{r.source}，查询于 {t}")

    for tk in state.get("ticket_results", []):
        label = tk.source or "景区官方"
        suffix = f"（{tk.updated_at}）" if tk.updated_at else ""
        out.append(f"票价：{tk.attraction}，来源 {label}{suffix}")

    seen_know: set = set()
    for d in state.get("retrieved_documents", []):
        meta = d.get("metadata", {})
        url = meta.get("source_url")
        if url and url not in seen_know:
            seen_know.add(url)
            out.append(f"知识：{meta.get('attraction')}（{url}）")
    return out
