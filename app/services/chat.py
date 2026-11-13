"""对话式行程规划服务（持久化 + 短期记忆 + 长期记忆）。

复用 LangGraph 多 Agent 规划能力，在其上叠加多轮「增量调整」：
- 首轮：根据表单或自然语言生成方案；
- 后续轮：理解用户修改诉求（不去某景点/区县、最远单程 X 公里、问充电桩/加油站），
  合并进既有约束后重新规划，或直接回答 POI 询问。

本阶段改造：
- 会话 / 消息 / 最近行程写入数据库（Store），服务重启后可继续；
- 所有读写都校验 user_id，杜绝仅凭 session_id 越权；
- 短期记忆 = 最近 N 条消息 + 校验过的摘要 + 结构化约束；
- 长期记忆只注入「已确认」的稳定偏好，当前输入优先。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from uuid import uuid4

from app.config import get_settings
from app.schemas import (
    ChatResponse,
    MemorySuggestion,
    PlanRequest,
    PlanResponse,
    POIResult,
)
from app.services.memory import propose_preferences
from app.services.storage import MemoryStore, SessionRecord, Store, get_store
from app.services.summary import build_context, deterministic_summary, llm_summary

logger = logging.getLogger(__name__)

# 石家庄主要区/县，用于「不去某区域」等关键词兜底匹配。
_DISTRICTS = [
    "长安区", "桥西区", "新华区", "裕华区", "藁城区", "鹿泉区", "栾城区", "井陉矿区",
    "正定", "赵县", "平山", "井陉", "灵寿", "赞皇", "元氏", "行唐", "无极", "深泽",
]


class SessionAccessError(Exception):
    """会话不存在或不属于当前用户。"""


@dataclass
class Refinement:
    exclude_attractions: list[str] = field(default_factory=list)
    exclude_districts: list[str] = field(default_factory=list)
    max_one_way_drive_km: float | None = None
    wants_charging: bool = False
    wants_gas: bool = False
    wants_replan: bool = True


@dataclass
class _Conversation:
    id: str
    state: dict = field(default_factory=dict)
    last_plan: PlanResponse | None = None
    summary: str = ""
    recent: list[dict] = field(default_factory=list)
    user_id: str | None = None
    is_new: bool = False


def form_to_state(form: PlanRequest) -> dict:
    return {
        "departure": form.departure,
        "travel_date": form.travel_date,
        "people_count": form.people_count,
        "budget": form.budget,
        "interests": form.interests,
        "transport_mode": form.transport_mode,
        "physical_level": form.physical_level,
        "has_elderly": form.has_elderly,
        "has_children": form.has_children,
        "latest_return_time": form.latest_return_time,
        "start_time": form.start_time,
        "exclude_attractions": form.exclude_attractions,
        "exclude_districts": form.exclude_districts,
        "max_one_way_drive_km": form.max_one_way_drive_km,
    }


def _hydrate_state(state: dict) -> dict:
    """从数据库 JSON 恢复时还原 date / Decimal，避免字符串进入确定性计算。"""
    from datetime import date
    from decimal import Decimal, InvalidOperation

    data = dict(state)
    travel_date = data.get("travel_date")
    if isinstance(travel_date, str):
        try:
            data["travel_date"] = date.fromisoformat(travel_date)
        except ValueError:
            data.pop("travel_date", None)
    budget = data.get("budget")
    if isinstance(budget, str):
        try:
            data["budget"] = Decimal(budget)
        except InvalidOperation:
            data.pop("budget", None)
    return data


class ChatService:
    def __init__(self, deps, graph, store: Store | None = None) -> None:
        self.deps = deps
        self.graph = graph
        self.store = store or get_store()
        self.settings = get_settings()
        # 未登录用户的临时会话（进程内，不持久化，也不可跨用户访问）
        self._anon: dict[str, _Conversation] = {}

    # ------------------------------------------------------------------ 入口
    def handle(
        self,
        message: str,
        session_id: str | None,
        form: PlanRequest | None,
        user_id: str | None = None,
    ) -> ChatResponse:
        conv = (
            self._open_user_conversation(message, session_id, form, user_id)
            if user_id
            else self._open_anon_conversation(message, session_id, form)
        )

        # 首轮：直接生成方案，不做 refinement 解析
        if conv.is_new:
            return self._first_turn(conv, message, form, user_id)

        if user_id:
            self.store.append_message(conv.id, user_id, "user", message)

        # 短期记忆：最近消息 + 摘要 + 结构化约束
        conv.state = dict(conv.state)
        conv.state["conversation_summary"] = conv.summary
        conv.state["recent_messages"] = [
            {"role": m["role"], "content": m["content"][:300]} for m in conv.recent
        ]

        refinement = self._parse_refinement(conv, message)

        if refinement.wants_charging or refinement.wants_gas:
            reply, pois = self._answer_poi(conv, refinement)
            if user_id:
                self.store.append_message(
                    conv.id, user_id, "assistant", reply,
                    {"poi_results": [p.model_dump(mode="json") for p in pois]},
                )
            return ChatResponse(session_id=conv.id, reply=reply, poi_results=pois)

        self._apply_refinement(conv, refinement)
        plan = self._run_plan(conv.state)
        conv.last_plan = plan
        reply = self._compose_reply(plan, refinement)

        suggestions: list[MemorySuggestion] = []
        if user_id:
            meta = {"itinerary_options": [o.model_dump(mode="json") for o in plan.itinerary_options]}
            self.store.append_message(conv.id, user_id, "assistant", reply, meta)
            self.store.update_session(
                conv.id,
                user_id,
                state=conv.state,
                last_plan=plan.model_dump(mode="json"),
            )
            self._maybe_update_summary(conv, user_id)
        else:
            conv.recent.append({"role": "assistant", "content": reply})

        return ChatResponse(
            session_id=conv.id,
            reply=reply,
            clarification_required=plan.clarification_required,
            clarification_question=plan.clarification_question,
            itinerary_options=plan.itinerary_options,
            memory_suggestions=suggestions,
        )

    def _first_turn(
        self,
        conv: _Conversation,
        message: str,
        form: PlanRequest | None,
        user_id: str | None,
    ) -> ChatResponse:
        plan = self._run_plan(conv.state)
        conv.last_plan = plan
        reply = self._compose_reply(plan)
        suggestions: list[MemorySuggestion] = []

        if user_id:
            self.store.append_message(conv.id, user_id, "user", message)
            self.store.append_message(
                conv.id,
                user_id,
                "assistant",
                reply,
                {"itinerary_options": [o.model_dump(mode="json") for o in plan.itinerary_options]},
            )
            self.store.update_session(
                conv.id,
                user_id,
                state=conv.state,
                last_plan=plan.model_dump(mode="json"),
            )
            suggestions = self._propose_memories(conv, form, user_id)
        else:
            conv.recent.append({"role": "user", "content": message})
            conv.recent.append({"role": "assistant", "content": reply})

        return ChatResponse(
            session_id=conv.id,
            reply=reply,
            clarification_required=plan.clarification_required,
            clarification_question=plan.clarification_question,
            itinerary_options=plan.itinerary_options,
            memory_suggestions=suggestions,
        )

    # ---------------------------------------------------------------- 会话
    def _open_user_conversation(
        self,
        message: str,
        session_id: str | None,
        form: PlanRequest | None,
        user_id: str,
    ) -> _Conversation:
        if session_id:
            record = self.store.get_session(session_id, user_id)
            if record is None:
                raise SessionAccessError("session not found")
            return self._from_record(record, user_id)

        state = form_to_state(form) if form is not None else {"user_request": message}
        state["long_term_preferences"] = self._confirmed_preferences(user_id)
        record = self.store.create_session(user_id, self._title_from(message, form), state)
        return _Conversation(
            id=record.id, state=_hydrate_state(record.state), user_id=user_id, is_new=True
        )

    def _from_record(self, record: SessionRecord, user_id: str) -> _Conversation:
        last_plan = None
        if record.last_plan:
            try:
                last_plan = PlanResponse.model_validate(record.last_plan)
            except Exception:  # noqa: BLE001 - 旧数据损坏不应阻断会话
                logger.warning("会话 %s 的历史方案无法解析，忽略", record.id)
        recent = [
            {"role": m.role, "content": m.content}
            for m in self.store.list_messages(
                record.id, user_id, limit=self.settings.recent_message_limit
            )
        ]
        return _Conversation(
            id=record.id,
            state=_hydrate_state(record.state or {}),
            last_plan=last_plan,
            summary=record.summary or "",
            recent=recent,
            user_id=user_id,
        )

    def _open_anon_conversation(
        self, message: str, session_id: str | None, form: PlanRequest | None
    ) -> _Conversation:
        if session_id and session_id in self._anon:
            return self._anon[session_id]

        state = form_to_state(form) if form is not None else {"user_request": message}
        conv = _Conversation(id=session_id or str(uuid4()), state=state, is_new=True)
        self._anon[conv.id] = conv
        return conv

    def _title_from(self, message: str, form: PlanRequest | None) -> str:
        if form is not None:
            return f"{form.departure} · {form.travel_date}"
        text = (message or "").strip().replace("\n", " ")
        return text[:30] or "新的行程"

    # ---------------------------------------------------------------- 记忆
    def _confirmed_preferences(self, user_id: str) -> list[dict]:
        return [
            {"type": m.type, "content": m.content}
            for m in self.store.list_memories(user_id, status="confirmed")
        ]

    def _propose_memories(
        self, conv: _Conversation, form: PlanRequest | None, user_id: str
    ) -> list[MemorySuggestion]:
        existing = {
            (m.type, m.content)
            for m in self.store.list_memories(user_id)
            if m.status != "deleted"
        }
        suggestions: list[MemorySuggestion] = []
        for item in propose_preferences(form, conv.state):
            if (item["type"], item["content"]) in existing:
                continue
            record = self.store.create_memory(
                user_id,
                item["type"],
                item["content"],
                source_session_id=conv.id,
                status="pending",
            )
            suggestions.append(
                MemorySuggestion(
                    id=record.id, type=record.type, content=record.content, status=record.status
                )
            )
        return suggestions

    def _maybe_update_summary(self, conv: _Conversation, user_id: str) -> None:
        """消息足够多时压缩较早部分为摘要；原始消息永不删除。"""
        limit = self.settings.recent_message_limit
        total = self.store.count_messages(conv.id, user_id)
        if total <= limit + 2:
            return
        older = self.store.list_messages(conv.id, user_id)[: total - limit]
        llm = self.deps.llm if self.settings.enable_llm_summary else None
        summary = llm_summary(llm, older) or deterministic_summary(older)
        if summary and summary != conv.summary:
            self.store.update_session(conv.id, user_id, summary=summary)
            conv.summary = summary

    # ---------------------------------------------------------------- 规划
    def _run_plan(self, state: dict) -> PlanResponse:
        result = self.graph.invoke(state)
        return result.get("response") or PlanResponse()

    def _apply_refinement(self, conv: _Conversation, r: Refinement) -> None:
        state = dict(conv.state)
        state["exclude_attractions"] = list(
            dict.fromkeys(state.get("exclude_attractions", []) + r.exclude_attractions)
        )
        state["exclude_districts"] = list(
            dict.fromkeys(state.get("exclude_districts", []) + r.exclude_districts)
        )
        if r.max_one_way_drive_km is not None:
            state["max_one_way_drive_km"] = r.max_one_way_drive_km
        conv.state = state

    # ------------------------------------------------------------ 意图理解
    def _parse_refinement(self, conv: _Conversation, message: str) -> Refinement:
        llm = self.deps.llm
        if llm and getattr(llm, "enabled", False):
            context = build_context(
                [_Msg(m["role"], m["content"]) for m in conv.recent],
                limit=self.settings.recent_message_limit,
            ).as_text()
            raw = llm.parse_refinement(message, context=context)
            if raw:
                return Refinement(
                    exclude_attractions=raw.get("exclude_attractions", []),
                    exclude_districts=raw.get("exclude_districts", []),
                    max_one_way_drive_km=raw.get("max_one_way_drive_km"),
                    wants_charging=bool(raw.get("wants_charging")),
                    wants_gas=bool(raw.get("wants_gas")),
                    wants_replan=bool(raw.get("wants_replan", True)),
                )
        return self._fallback_refine(conv, message)

    def _fallback_refine(self, conv: _Conversation, message: str) -> Refinement:
        """关键词兜底：无需 LLM 时也能处理常见诉求。"""
        r = Refinement()
        r.wants_charging = bool(re.search(r"充电|桩", message))
        r.wants_gas = bool(re.search(r"加油|汽油|气站", message))
        m = re.search(r"(\d+(?:\.\d+)?)\s*(公里|千米|km)", message, re.IGNORECASE)
        if m:
            try:
                r.max_one_way_drive_km = float(m.group(1))
            except ValueError:
                r.max_one_way_drive_km = None
        r.exclude_districts = [d for d in _DISTRICTS if d in message]
        known = self._known_attraction_names(conv)
        r.exclude_attractions = [n for n in known if n and n in message]
        # 兜底：显式「不去 X」但 X 不在当前方案中时，按文本片段直接排除
        for token in re.findall(r"(?:不想去|不去|排除|别去)\s*([\u4e00-\u9fa5A-Za-z0-9]{2,10})", message):
            token = token.strip()
            if token and token not in r.exclude_attractions:
                r.exclude_attractions.append(token)
        return r

    def _known_attraction_names(self, conv: _Conversation) -> list[str]:
        names: list[str] = []
        plan = conv.last_plan
        if plan:
            for opt in plan.itinerary_options:
                names.extend(a.name for a in opt.attractions)
        try:
            from app.tools.attraction_search import load_attractions

            names.extend(a.name for a in load_attractions())
        except Exception:  # noqa: BLE001 - 知识文件缺失时只用方案内名称
            pass
        return list(dict.fromkeys(names))

    # ------------------------------------------------------------ POI 回答
    def _answer_poi(self, conv: _Conversation, r: Refinement) -> tuple[str, list[POIResult]]:
        keywords: list[str] = []
        if r.wants_charging:
            keywords.append("充电站")
        if r.wants_gas:
            keywords.append("加油站")

        pois: list[POIResult] = []
        for attr in self._top_option_attractions(conv):
            if attr.longitude is None or attr.latitude is None:
                continue
            for kw in keywords:
                pois.extend(
                    self.deps.amap.search_nearby(
                        kw, attr.longitude, attr.latitude, radius_m=3000, limit=3
                    )
                )

        if not pois:
            reply = "抱歉，当前未能获取到沿途充电桩/加油站实时数据（可能未配置高德 Key 或服务不可用），请以出行当天导航为准。"
        else:
            seen: set[str] = set()
            lines: list[str] = []
            for p in pois:
                key = f"{p.name}|{p.address}"
                if key in seen:
                    continue
                seen.add(key)
                lines.append(f"- {p.name}（{p.address or '地址未知'}）")
            reply = "为您在景点周边找到以下充电桩/加油站（来源：高德地图）：\n" + "\n".join(lines)
        return reply, pois

    def _top_option_attractions(self, conv: _Conversation):
        plan = conv.last_plan
        if not plan or not plan.itinerary_options:
            return []
        return plan.itinerary_options[0].attractions

    # ------------------------------------------------------------- 回复生成
    def _compose_reply(self, plan: PlanResponse, refinement: Refinement | None = None) -> str:
        if plan.clarification_required:
            return plan.clarification_question or "请补充出行信息。"

        changes: list[str] = []
        if refinement is not None:
            if refinement.exclude_attractions:
                changes.append("已排除景点：" + "、".join(refinement.exclude_attractions))
            if refinement.exclude_districts:
                changes.append("不去区域：" + "、".join(refinement.exclude_districts))
            if refinement.max_one_way_drive_km is not None:
                changes.append(f"单程最远不超过 {refinement.max_one_way_drive_km} 公里")

        if not plan.itinerary_options:
            base = plan.explanation or "抱歉，在当前约束下没有生成可行方案，请放宽限制或更换兴趣后重试。"
        else:
            opt = plan.itinerary_options[0]
            names = " → ".join(a.name for a in opt.attractions) or "（暂无景点）"
            base = (
                f"为您推荐「{opt.title}」：{names}。"
                f"预计 {opt.return_time or '-'} 返回，全天驾车约 {opt.total_drive_minutes} 分钟。"
            )
            if opt.budget and opt.budget.is_lower_bound:
                base += (
                    f"预算为已知费用下限（{opt.budget.unknown_ticket_count} 个景点门票未知），"
                    "实际可能更高。"
                )
            if len(plan.itinerary_options) > 1:
                base += f"（另有 {len(plan.itinerary_options) - 1} 个备选方案）"

        if changes:
            return "已根据您的要求调整方案。\n" + "；".join(changes) + "。\n\n" + base
        return base


@dataclass
class _Msg:
    """摘要工具需要的最小消息结构。"""

    role: str
    content: str


__all__ = ["ChatService", "MemoryStore", "SessionAccessError", "form_to_state"]
