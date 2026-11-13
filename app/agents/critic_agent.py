"""CriticAgent：审查方案质量并给出 ACCEPT / REPLAN / RESEARCH 建议。

- 只能「建议」；Python 确定性校验器（预算/时间冲突/最晚返程/驾车距离时长/
  老人儿童/午餐/景点数/未知票价/营业时间缺失/路线可行性）为最高优先级。
- RESEARCH：知识不足 / 相关度过低 / 候选不足。
- REPLAN：知识充分但顺序/组合/时长不合理（含硬约束失败）。
- ACCEPT：硬约束通过且无严重事实问题。
"""
from __future__ import annotations

from typing import Any

from app.agents.base import Agent
from app.agents.schemas import CriticReport
from app.agents.support import (
    knowledge_section,
    proposal_summary,
    realtime_section,
    request_from_requirement,
    user_section,
)
from app.services.relevance import is_relevant


class CriticAgent(Agent):
    name = "CriticAgent"

    def run(self, state: dict, deps: Any) -> dict:
        req = state.get("requirement_result")
        options = state.get("itinerary_options") or []
        knowledge = state.get("knowledge_result")
        realtime = state.get("realtime_result")

        # ---- Python 硬约束（最高优先级）----
        blocking: list[str] = []
        soft: list[str] = []
        if req is not None and options:
            request = request_from_requirement(req)
            for opt in options:
                for issue in deps.validator.validate(opt, request):
                    if issue.blocking:
                        blocking.append(issue.message)
                    else:
                        soft.append(issue.message)

        # ---- 兴趣相关性（确定性）：不相关景点必须重新规划 ----
        if req is not None and req.interests and options:
            for opt in options:
                bad = [a.name for a in opt.attractions if not is_relevant(a, req.interests)]
                if bad:
                    blocking.append(
                        f"方案「{opt.title}」包含与兴趣（{'、'.join(req.interests)}）"
                        f"不相关的景点：{'、'.join(bad)}"
                    )

        # ---- 知识充分性 ----
        knowledge_sufficient = knowledge.sufficient if knowledge is not None else True
        unsupported = self._unsupported_claims(state, knowledge)

        # ---- 可选 LLM 软性反馈（不改变决策）----
        llm_feedback_planner = ""
        llm_feedback_retriever = ""
        llm_soft: list[str] = []
        LLM = getattr(deps, "llm", None)
        if LLM and getattr(LLM, "enabled", False) and getattr(getattr(deps, "settings", None), "enable_llm_critic", False):
            try:
                cr = LLM.critic(
                    user_section(req) if req else "",
                    proposal_summary(knowledge),
                    knowledge_section(knowledge),
                    realtime_section(realtime, state.get("ranked_candidates") or []),
                )
                if cr:
                    llm_feedback_planner = cr.get("feedback_for_planner", "")
                    llm_feedback_retriever = cr.get("feedback_for_retriever", "")
                    llm_soft = cr.get("soft_issues") or []
            except Exception:  # noqa: BLE001 - 批评审失败不影响决策
                llm_soft = []

        # ---- 决策 ----
        if not knowledge_sufficient:
            decision = "RESEARCH"
            route_reason = "知识不足/候选不足，需重新检索"
        elif blocking:
            decision = "REPLAN"
            route_reason = "硬约束未通过，需重新规划"
        else:
            decision = "ACCEPT"
            route_reason = "硬约束通过且无严重事实问题"

        feedback_planner = "; ".join(blocking) if blocking else ""
        if not feedback_planner:
            feedback_planner = "; ".join(unsupported)
        if llm_feedback_planner and llm_feedback_planner not in feedback_planner:
            feedback_planner = (feedback_planner + "；" if feedback_planner else "") + llm_feedback_planner

        report = CriticReport(
            decision=decision,
            blocking_issues=blocking,
            soft_issues=soft + [f"LLM:{s}" for s in llm_soft],
            unsupported_claims=unsupported,
            feedback_for_planner=feedback_planner,
            feedback_for_retriever=llm_feedback_retriever,
            route_reason=route_reason,
        )

        return {
            "critic_report": report,
            "validation_errors": [{"message": b} for b in blocking],
            "validation_risks": [{"message": s} for s in soft],
            "current_agent": self.name,
            "next_agent": "Supervisor",
            "route_reason": route_reason,
        }

    @staticmethod
    def _unsupported_claims(state: dict, knowledge: Any) -> list[str]:
        """找出缺乏知识/实时数据支撑的推荐表述（软性信息，不直接阻断）。"""
        known_attractions = set()
        if knowledge is not None:
            known_attractions.update(d.attraction for d in knowledge.documents if d.attraction)
        realtime = state.get("realtime_result")
        if realtime is not None:
            known_attractions.update(realtime.route_map.keys())
        claims: list[str] = []
        for opt in state.get("itinerary_options") or []:
            for a in opt.attractions:
                if a.name not in known_attractions:
                    claims.append(f"方案「{opt.title}」中的 {a.name} 缺少知识/实时数据支撑")
        return claims
