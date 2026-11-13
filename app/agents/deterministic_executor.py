"""DeterministicExecutor：把 PlannerAgent 的选点顺序变成可交付行程。

- 全部数字（时间轴/路线距离/驾车时间/午餐与缓冲/预算明细/返程/风险）由
  Python 确定性计算（复用 itinerary.py 确定性调度器），LLM 永远不能覆盖。
- suggested_duration_minutes 由 Pydantic 限制在 30~300 分钟。
"""
from __future__ import annotations

from typing import Any

from app.agents.base import Agent
from app.agents.support import build_context, tickets_from_realtime
from app.services.itinerary import _build_option

_MAX_DURATION = 300
_MIN_DURATION = 30


class DeterministicExecutor(Agent):
    name = "DeterministicExecutor"

    def run(self, state: dict, deps: Any) -> dict:
        req = state.get("requirement_result")
        proposal = state.get("plan_proposal")
        ranked = state.get("ranked_candidates") or []
        realtime = state.get("realtime_result")

        chosen_by_name = {c.attraction.name: c for c in ranked}
        tickets = tickets_from_realtime(realtime)
        ctx = build_context(req, state, deps)

        options: list = []
        routes: list = []
        for opt in (proposal.options if proposal else []):
            chosen: list = []
            for item in opt.attractions:
                cand = chosen_by_name.get(item.name)
                if cand is None:
                    continue
                cand.attraction.suggested_duration_minutes = _clamp(item.suggested_duration_minutes)
                chosen.append(cand)
            if not chosen:
                continue
            built, built_routes = _build_option(opt.title, chosen, ctx, tickets, ranked)
            if opt.reason:
                built.reason = opt.reason
            options.append(built)
            routes.extend(built_routes)

        summary = {
            "option_titles": [o.title for o in options],
            "option_count": len(options),
        }
        return {
            "itinerary_options": options,
            "route_results": routes,
            "deterministic_result": summary,
            "current_agent": self.name,
            "next_agent": "CriticAgent",
            "route_reason": "确定性调度完成",
        }


def _clamp(value: Any) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        v = 90
    return max(_MIN_DURATION, min(_MAX_DURATION, v))
