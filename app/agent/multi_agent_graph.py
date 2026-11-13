"""多 Agent 图构建：Supervisor 协调的 LangGraph。

START -> RequirementAgent ->(不完整) FinalResponse(追问) / (完整) -> Supervisor
     -> KnowledgeAgent -> RealtimeAgent -> PlannerAgent -> DeterministicExecutor -> CriticAgent -> Supervisor
Supervisor 路由：ACCEPT -> FinalResponse；REPLAN -> PlannerAgent；
        RESEARCH -> KnowledgeAgent；超限 -> DeterministicFallback -> FinalResponse。
所有循环有界（RESEARCH≤2、REPLAN≤3），禁止无限循环。
"""
from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from app.agent.nodes import _build_citations
from app.agent.state import TravelState
from app.agents.base import Agent
from app.agents.critic_agent import CriticAgent
from app.agents.deterministic_executor import DeterministicExecutor
from app.agents.knowledge_agent import KnowledgeAgent
from app.agents.planner_agent import PlannerAgent
from app.agents.realtime_agent import RealtimeAgent
from app.agents.requirement_agent import RequirementAgent
from app.agents.supervisor import DeterministicFallback, Supervisor
from app.schemas import PlanResponse
from app.services.feasibility import filter_feasible

_NO_PLAN_MESSAGE = "没有找到同时满足预算、最晚返程与驾车时长等硬约束的方案，请调整条件后重试。"


class MultiAgentState(TravelState):
    # 需求
    include_districts: list[str]
    requirement_result: Any
    requirement_complete: bool
    # 各 Agent 产物
    knowledge_result: Any
    realtime_result: Any
    plan_proposal: Any
    deterministic_result: Any
    critic_report: Any
    # 编排
    research_iteration: int
    planning_iteration: int
    current_agent: str
    next_agent: str
    route_reason: str
    degraded_services: list[str]
    agent_trace: list
    knowledge_feedback: str
    planner_feedback: str
    retrieval_query: str
    explanation: str


def _after_requirement(state: dict) -> str:
    return "clarify" if state.get("requirement_complete") is False else "complete"


def _route_next(state: dict) -> str:
    return state.get("next_agent") or "KnowledgeAgent"


def _wrap(agent: Agent, deps: Any):
    def node(state: dict) -> dict:
        return agent.execute(state, deps)

    return node


class FinalResponse(Agent):
    name = "FinalResponse"

    def run(self, state: dict, deps: Any) -> dict:
        if state.get("clarification_required"):
            return {
                "response": PlanResponse(
                    clarification_required=True,
                    missing_fields=state.get("missing_fields", []),
                    clarification_question=state.get("clarification_question"),
                )
            }

        # 统一出口门禁：无论方案来自 LLM / REPLAN / 确定性兜底，
        # 都必须再过一次确定性硬约束校验，不合格方案直接剔除。
        options = list(state.get("itinerary_options") or [])
        explanation = state.get("explanation")
        req = state.get("requirement_result")
        if req is not None:
            from app.agents.support import request_from_requirement
            from app.services.relevance import is_relevant

            outcome = filter_feasible(
                options, request_from_requirement(req), deps.validator
            )
            options = outcome.options
            dropped_for_interest = False
            if req.interests:
                kept = [
                    opt
                    for opt in options
                    if all(is_relevant(a, req.interests) for a in opt.attractions)
                ]
                dropped_for_interest = len(kept) != len(options)
                options = kept

            if options:
                explanation = None
            elif dropped_for_interest and not outcome.message:
                explanation = (
                    f"没有找到符合兴趣（{'、'.join(req.interests)}）"
                    "且满足预算与返程约束的方案，请更换兴趣或放宽条件后重试。"
                )
            else:
                explanation = outcome.message or explanation or _NO_PLAN_MESSAGE
        elif not options:
            explanation = explanation or _NO_PLAN_MESSAGE

        citations = _build_citations(state)
        risks = [
            d.get("message")
            for d in state.get("validation_risks", [])
            if isinstance(d, dict)
        ]
        for opt in options:
            for msg in risks:
                if msg and msg not in opt.risks:
                    opt.risks.append(msg)
            if citations and citations != opt.citations:
                opt.citations = list(citations)

        return {
            "response": PlanResponse(itinerary_options=options, explanation=explanation),
            "citations": citations,
            "next_agent": None,
            "route_reason": "",
        }


def build_multi_agent_graph(deps: Any):
    g = StateGraph(MultiAgentState)
    g.add_node("requirement_agent", _wrap(RequirementAgent(), deps))
    g.add_node("supervisor", _wrap(Supervisor(), deps))
    g.add_node("knowledge_agent", _wrap(KnowledgeAgent(), deps))
    g.add_node("realtime_agent", _wrap(RealtimeAgent(), deps))
    g.add_node("planner_agent", _wrap(PlannerAgent(), deps))
    g.add_node("deterministic_executor", _wrap(DeterministicExecutor(), deps))
    g.add_node("critic_agent", _wrap(CriticAgent(), deps))
    g.add_node("deterministic_fallback", _wrap(DeterministicFallback(), deps))
    g.add_node("final_response", _wrap(FinalResponse(), deps))

    g.add_edge(START, "requirement_agent")
    g.add_conditional_edges(
        "requirement_agent",
        _after_requirement,
        {"clarify": "final_response", "complete": "supervisor"},
    )
    g.add_conditional_edges(
        "supervisor",
        _route_next,
        {
            "KnowledgeAgent": "knowledge_agent",
            "PlannerAgent": "planner_agent",
            "FinalResponse": "final_response",
            "DeterministicFallback": "deterministic_fallback",
        },
    )
    g.add_edge("knowledge_agent", "realtime_agent")
    g.add_edge("realtime_agent", "planner_agent")
    g.add_edge("planner_agent", "deterministic_executor")
    g.add_edge("deterministic_executor", "critic_agent")
    g.add_edge("critic_agent", "supervisor")
    g.add_edge("deterministic_fallback", "final_response")
    g.add_edge("final_response", END)
    return g.compile()
