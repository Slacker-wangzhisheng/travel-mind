"""LangGraph 状态类型定义。"""
from __future__ import annotations

from typing import Any, TypedDict


class TravelState(TypedDict, total=False):
    # ---- 用户输入 ----
    user_request: str
    departure: str
    travel_date: Any  # datetime.date
    people_count: int
    budget: Any  # Decimal
    interests: list[str]
    transport_mode: str
    physical_level: int
    has_elderly: bool
    has_children: bool
    latest_return_time: str
    start_time: str
    # 多轮对话约束
    exclude_attractions: list[str]
    exclude_districts: list[str]
    max_one_way_drive_km: float | None

    # ---- 澄清 ----
    missing_fields: list[str]
    clarification_required: bool
    clarification_question: str

    # ---- 中间产物 ----
    weather: Any  # WeatherResult | None
    weather_error: Any  # WeatherError | None
    candidate_attractions: list[Any]  # list[Attraction]
    retrieved_documents: list[Any]
    ranked_candidates: list[Any]  # list[Candidate]
    route_results: list[Any]  # list[RouteResult]
    ticket_results: list[Any]  # list[TicketInfo]

    itinerary_options: list[Any]  # list[ItineraryOption]
    itinerary_max_attractions: int
    validation_errors: list[Any]  # list[ValidationIssue]
    validation_risks: list[Any]
    citations: list[Any]  # list[Citation]
    iteration_count: int
    max_revision_iterations: int

    # ---- 输出 ----
    response: Any  # PlanResponse | None
