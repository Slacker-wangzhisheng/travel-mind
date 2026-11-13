"""PlannerAgent：综合需求/RAG/实时结果生成两套结构化提案。

- 只决定景点选择、顺序、suggested_duration_minutes、推荐理由；
  不计算最终路线/时间轴/预算。
- LLM 返回的景点名必须过候选白名单，未知名称直接拒绝。
- LLM 失败 / JSON 非法 / 未启用时走确定性兜底。
- suggested_duration_minutes 用 Pydantic 限制在 30~300 分钟内。
"""
from __future__ import annotations

import logging
from typing import Any

from app.agents.base import Agent
from app.agents.schemas import PlanAttraction, PlanOption, PlanProposal
from app.agents.support import (
    build_ranked_candidates,
    knowledge_section,
    realtime_section,
    user_section,
)
from app.utils.redact import redact

logger = logging.getLogger(__name__)

_MAX_DURATION = 300
_MIN_DURATION = 30
_MAX_ATTRACTIONS = 3


class PlannerAgent(Agent):
    name = "PlannerAgent"

    def run(self, state: dict, deps: Any) -> dict:
        req = state.get("requirement_result")
        knowledge = state.get("knowledge_result")
        realtime = state.get("realtime_result")
        feedback = state.get("planner_feedback") or ""

        candidates = list((knowledge.candidate_attractions if knowledge else None) or [])
        if not candidates:
            candidates = list(state.get("candidate_attractions") or [])

        # REPLAN 时收缩范围，保证循环收敛到 ACCEPT
        planning = int(state.get("planning_iteration") or 0)
        allow_full = int(state.get("itinerary_max_attractions") or _MAX_ATTRACTIONS)
        allowance = max(1, allow_full - planning)

        ranked = build_ranked_candidates(
            candidates, (req.interests if req else []), bool(req.has_elderly) if req else False
        )
        whitelist = {c.attraction.name: c for c in ranked}

        proposal: PlanProposal | None = None
        notes: list[str] = []
        llm = getattr(deps, "llm", None)
        if llm and getattr(llm, "enabled", False) and ranked:
            try:
                parsed = llm.plan_itinerary(
                    user_section(req) if req else "",
                    knowledge_section(knowledge),
                    realtime_section(realtime, ranked),
                    feedback,
                )
                if parsed and parsed.get("options"):
                    proposal = self._from_llm(parsed, whitelist, knowledge, allowance)
                    notes.append("LLM 提案通过白名单校验")
            except Exception as exc:  # noqa: BLE001 - 任何失败走确定性兜底
                logger.warning("PlannerAgent LLM 生成失败：%s", redact(exc))

        if proposal is None or not proposal.options:
            proposal = self._deterministic(ranked, allowance, planning)
            notes.append("确定性兜底提案")

        proposal.generation_notes = notes

        return {
            "plan_proposal": proposal,
            "ranked_candidates": ranked,
            "itinerary_max_attractions": allowance,
            "current_agent": self.name,
            "next_agent": "DeterministicExecutor",
            "route_reason": "提案就绪",
        }

    # ---------------------------------------------------------------- LLM
    @staticmethod
    def _from_llm(parsed: dict, whitelist: dict, knowledge: Any, allowance: int) -> PlanProposal:
        doc_ids_by_attraction: dict[str, list[str]] = {}
        all_ids: list[str] = []
        if knowledge is not None:
            for d in knowledge.documents:
                if d.attraction:
                    doc_ids_by_attraction.setdefault(d.attraction, []).append(d.doc_id)
                    all_ids.append(d.doc_id)

        options: list[PlanOption] = []
        for opt in parsed.get("options") or []:
            items: list[PlanAttraction] = []
            for item in opt.get("attractions") or []:
                if len(items) >= allowance:
                    break
                name = str(item.get("name") or "").strip()
                cand = whitelist.get(name)
                if cand is None:
                    continue  # 未知名称直接拒绝，绝不编造候选外景点
                duration = _clamp(int(item.get("suggested_duration_minutes", cand.attraction.suggested_duration_minutes)))
                items.append(
                    PlanAttraction(
                        attraction_id=cand.attraction.name,
                        name=cand.attraction.name,
                        suggested_duration_minutes=duration,
                        note=str(item.get("note") or "")[:300],
                        supporting_source_ids=doc_ids_by_attraction.get(cand.attraction.name, []),
                    )
                )
            if not items:
                continue
            options.append(
                PlanOption(
                    title=str(opt.get("title") or "")[:50],
                    reason=str(opt.get("reason") or "")[:200],
                    attractions=items,
                )
            )
        used = list(dict.fromkeys(all_ids))
        return PlanProposal(options=options, used_document_ids=used)

    # -------------------------------------------------------- 确定性兜底
    @staticmethod
    def _deterministic(ranked: list, allowance: int, planning: int = 0) -> PlanProposal:
        """按相关度选点；第二套用「次优相关景点」，不再按体力挑不相关景点。

        REPLAN 时按游览时长从短到长重排：返程超时/预算不足多半来自耗时最长的
        景点，继续重复同一提案只会空转（用户要求「继续思考，不要想一次就输出」）。
        """
        pool = ranked
        if planning > 0:
            pool = sorted(
                ranked,
                key=lambda c: (c.attraction.suggested_duration_minutes, -c.score),
            )

        options: list[PlanOption] = []
        themed = pool[:allowance]
        if themed:
            options.append(_plan_option("方案一 · 主题精选", themed))

        used = {c.attraction.name for c in themed}
        rest = [c for c in pool if c.attraction.name not in used][: max(1, min(2, allowance))]
        if rest:
            options.append(_plan_option("方案二 · 备选组合", rest))
        else:
            easy = [c for c in pool if c.attraction.physical_level <= 2][: max(1, min(2, allowance))]
            if easy:
                options.append(_plan_option("方案二 · 轻松备选", easy))
        return PlanProposal(options=options)


def _plan_option(title: str, chosen: list) -> PlanOption:
    items = [
        PlanAttraction(
            attraction_id=c.attraction.name,
            name=c.attraction.name,
            suggested_duration_minutes=_clamp(c.attraction.suggested_duration_minutes),
        )
        for c in chosen
    ]
    return PlanOption(title=title, attractions=items)


def _clamp(value: int) -> int:
    return max(_MIN_DURATION, min(_MAX_DURATION, int(value)))
