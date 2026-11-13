"""多 Agent 工作流的结构化输入/输出模型（Pydantic 校验）。

领域共享模型见 app/schemas.py；此处仅放置各 Agent 之间传递的产物。
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas import (
    Attraction,
    RouteResult,
    TicketInfo,
    WeatherError,
    WeatherResult,
)

# ---------------------------------------------------------------------------
# Agent Handoff / 追踪
# ---------------------------------------------------------------------------


class AgentTrace(BaseModel):
    """一次 Agent 调用的元数据。禁止记录 API Key / 完整 Prompt / 隐藏思维链。"""

    agent_name: str
    started_at: datetime
    finished_at: datetime
    status: str = "ok"  # ok | degraded | error
    next_agent: str | None = None
    route_reason: str | None = None


# ---------------------------------------------------------------------------
# RequirementAgent
# ---------------------------------------------------------------------------


class RequirementResult(BaseModel):
    departure: str | None = None
    travel_date: date | None = None
    people_count: int | None = Field(default=None, ge=1, le=50)
    budget: Decimal | None = Field(default=None, gt=0)
    interests: list[str] = Field(default_factory=list)
    physical_level: int = Field(default=3, ge=1, le=5)
    has_elderly: bool = False
    has_children: bool = False
    latest_return_time: str = "21:00"
    start_time: str = "08:00"
    # 「想去」与「不去」区县严格分开，互不复用
    include_districts: list[str] = Field(default_factory=list)
    exclude_districts: list[str] = Field(default_factory=list)
    exclude_attractions: list[str] = Field(default_factory=list)
    max_one_way_drive_km: float | None = None

    complete: bool = False
    missing_fields: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# KnowledgeAgent
# ---------------------------------------------------------------------------


class KnowledgeDocument(BaseModel):
    text: str
    score: float = 0.0
    doc_id: str = ""
    attraction: str | None = None
    district: str | None = None
    category: str | None = None
    source_url: str | None = None
    updated_at: str | None = None


class KnowledgeResult(BaseModel):
    query: str = ""
    filters: dict[str, str] = Field(default_factory=dict)
    documents: list[KnowledgeDocument] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    candidate_attractions: list[Attraction] = Field(default_factory=list)
    ranked_candidates: list[Any] = Field(default_factory=list)  # list[Candidate]
    max_score: float = 0.0
    sufficient: bool = False
    retry_reason: str = ""
    degraded: bool = False
    rule_hits: int = 0
    vector_hits: int = 0


# ---------------------------------------------------------------------------
# RealtimeAgent
# ---------------------------------------------------------------------------


class RealtimeResult(BaseModel):
    weather: WeatherResult | None = None
    weather_error: WeatherError | None = None
    route_results: list[RouteResult] = Field(default_factory=list)
    ticket_results: list[TicketInfo] = Field(default_factory=list)
    route_map: dict[str, RouteResult] = Field(default_factory=dict)
    ticket_map: dict[str, TicketInfo] = Field(default_factory=dict)
    poi_map: dict[str, Any] = Field(default_factory=dict)
    failed_tools: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    queried_at: datetime | None = None


# ---------------------------------------------------------------------------
# PlannerAgent
# ---------------------------------------------------------------------------


class PlanAttraction(BaseModel):
    attraction_id: str = ""
    name: str
    suggested_duration_minutes: int = Field(default=90, ge=30, le=300)
    note: str = ""
    supporting_source_ids: list[str] = Field(default_factory=list)


class PlanOption(BaseModel):
    title: str
    reason: str = ""
    attractions: list[PlanAttraction] = Field(default_factory=list)


class PlanProposal(BaseModel):
    options: list[PlanOption] = Field(default_factory=list)
    generation_notes: list[str] = Field(default_factory=list)
    used_document_ids: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# CriticAgent
# ---------------------------------------------------------------------------

CriticDecision = Literal["ACCEPT", "REPLAN", "RESEARCH"]


class CriticReport(BaseModel):
    decision: CriticDecision = "ACCEPT"
    blocking_issues: list[str] = Field(default_factory=list)
    soft_issues: list[str] = Field(default_factory=list)
    unsupported_claims: list[str] = Field(default_factory=list)
    feedback_for_planner: str = ""
    feedback_for_retriever: str = ""
    route_reason: str = ""
