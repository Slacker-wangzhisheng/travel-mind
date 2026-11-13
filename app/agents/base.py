"""多 Agent 基类与统一追踪/降级包装。

每个 Agent 是独立类，职责单一；execute() 负责记录 agent_trace
（仅 name/started/finished/status/next_agent/route_reason），
并在运行异常时统一降级，避免把异常抛向图造成死循环。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from app.agents.schemas import AgentTrace
from app.utils.redact import redact

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now()


class Agent:
    """各 Agent 的基类。子类实现 run() 返回要合并进共享状态的字典。"""

    name: str = "agent"

    def run(self, state: dict, deps: Any) -> dict:
        raise NotImplementedError

    def _on_failure(self, state: dict, deps: Any, exc: Exception) -> dict:
        """Agent 抛异常时的默认降级：标记 degraded，不阻断流程。"""
        logger.warning("Agent %s 降级：%s", self.name, redact(exc))
        degraded = list(state.get("degraded_services") or [])
        if self.name not in degraded:
            degraded.append(self.name)
        return {"degraded_services": degraded}

    def execute(self, state: dict, deps: Any) -> dict:
        started = _now()
        trace = AgentTrace(agent_name=self.name, started_at=started, finished_at=started)
        try:
            out = self.run(dict(state), deps) or {}
            trace.status = "ok"
        except Exception as exc:  # noqa: BLE001 - 任何失败都必须降级而非抛穿
            trace.status = "error"
            out = self._on_failure(state, deps, exc)
            out = {**out, "current_agent": state.get("current_agent"), "next_agent": None}
        trace.next_agent = out.get("next_agent")
        trace.route_reason = out.get("route_reason")
        trace.finished_at = _now()

        previous = list(state.get("agent_trace") or [])
        entry = trace.model_dump()
        previous.append(entry)
        out["agent_trace"] = previous
        return out
