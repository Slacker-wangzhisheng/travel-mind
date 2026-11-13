"""Supervisor 与确定性兜底节点。

Supervisor：确定性路由（不依赖 LLM），维护 research_iteration / planning_iteration，
防止死循环，超限时转 DeterministicFallback。Agent Handoff 信息记录到 agent_trace。
DeterministicFallback：复用确定性调度器 build_options 直接产出可行方案。
"""
from __future__ import annotations

from typing import Any

from app.agents.base import Agent
from app.agents.support import (
    build_context,
    build_ranked_candidates,
    request_from_requirement,
    tickets_from_realtime,
)
from app.services.feasibility import build_feasible_options


class Supervisor(Agent):
    name = "Supervisor"

    def run(self, state: dict, deps: Any) -> dict:
        research = int(state.get("research_iteration") or 0)
        planning = int(state.get("planning_iteration") or 0)
        max_r = deps.settings.max_research_iterations
        max_p = deps.settings.max_planning_iterations
        critic = state.get("critic_report")

        out: dict = {"current_agent": self.name}

        # 首轮：进入知识检索流水线
        if critic is None:
            out.update(
                next_agent="KnowledgeAgent",
                research_iteration=0,
                planning_iteration=0,
                route_reason="启动知识检索流水线",
            )
            return out

        decision = critic.decision
        if decision == "ACCEPT":
            out.update(next_agent="FinalResponse", route_reason="ACCEPT：方案通过")
            return out

        if decision == "RESEARCH":
            if research >= max_r:
                out.update(next_agent="DeterministicFallback", route_reason=f"RESEARCH 超限（{research}）")
                return out
            out.update(
                next_agent="KnowledgeAgent",
                research_iteration=research + 1,
                knowledge_feedback=critic.feedback_for_retriever or "",
                planner_feedback="",
                route_reason=f"RESEARCH#{research + 1}",
            )
            return out

        if decision == "REPLAN":
            if planning >= max_p:
                out.update(next_agent="DeterministicFallback", route_reason=f"REPLAN 超限（{planning}）")
                return out
            out.update(
                next_agent="PlannerAgent",
                planning_iteration=planning + 1,
                planner_feedback=critic.feedback_for_planner or "",
                route_reason=f"REPLAN#{planning + 1}",
            )
            return out

        out.update(next_agent="FinalResponse", route_reason="未识别决策，直接输出")
        return out


class DeterministicFallback(Agent):
    name = "DeterministicFallback"

    def run(self, state: dict, deps: Any) -> dict:
        req = state.get("requirement_result")
        ranked = list(state.get("ranked_candidates") or [])
        if not ranked:
            candidates = list(state.get("candidate_attractions") or [])
            ranked = build_ranked_candidates(
                candidates, (req.interests if req else []), bool(req.has_elderly) if req else False
            )
        realtime = state.get("realtime_result")
        tickets = tickets_from_realtime(realtime)
        ctx = build_context(req, state, deps)

        # 兜底不能无条件取前三个景点：从多到少尝试，全部不合格时诚实返回空方案
        if req is None:
            return {
                "itinerary_options": [],
                "route_results": [],
                "deterministic_result": {"fallback": True, "option_count": 0},
                "explanation": "缺少结构化需求，无法生成确定性兜底方案。",
                "current_agent": self.name,
                "next_agent": "FinalResponse",
                "route_reason": "兜底失败：缺少需求",
            }

        options, routes, rejected = build_feasible_options(
            ranked,
            ctx,
            tickets,
            request_from_requirement(req),
            deps.validator,
            max_attractions=3,
        )
        explanation = None
        if not options:
            from app.services.feasibility import explain_no_options

            explanation = explain_no_options(rejected, request_from_requirement(req))

        return {
            "itinerary_options": options,
            "route_results": routes,
            "deterministic_result": {
                "fallback": True,
                "option_count": len(options),
                "rejected": rejected,
            },
            "explanation": explanation,
            "current_agent": self.name,
            "next_agent": "FinalResponse",
            "route_reason": "确定性兜底方案（已过滤硬约束）" if options else "确定性兜底：无可行方案",
        }
