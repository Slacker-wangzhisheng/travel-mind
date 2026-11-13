"""LangGraph 图构建与依赖装配。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from functools import partial
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.agent import nodes
from app.agent.state import TravelState
from app.config import Settings, get_settings
from app.rag.embeddings import MockEmbedding
from app.rag.ingest import ingest_attractions
from app.rag.retriever import MockVectorStore, Retriever
from app.services.validator import ItineraryValidator
from app.tools.amap import AMapTool
from app.tools.tickets import get_ticket_provider
from app.tools.weather import MockWeatherProvider

_NODES = [
    "parse_request",
    "check_required_fields",
    "retrieve_candidates",
    "fetch_weather",
    "fetch_live_information",
    "rank_candidates",
    "build_itinerary",
    "validate_itinerary",
    "revise_itinerary",
    "generate_response",
]


@dataclass
class GraphDeps:
    settings: Settings
    amap: AMapTool
    weather: Any
    tickets: Any
    retriever: Retriever
    validator: ItineraryValidator
    llm: Any = None


def _should_clarify(state: dict) -> str:
    return "clarify" if state.get("clarification_required") else "retrieve"


def _should_revise(state: dict) -> str:
    limit = state.get("max_revision_iterations", 3)
    if state.get("validation_errors") and state.get("iteration_count", 0) < limit:
        return "revise"
    return "respond"


def build_graph(deps: GraphDeps):
    g = StateGraph(TravelState)
    for name in _NODES:
        g.add_node(name, partial(getattr(nodes, name), deps=deps))

    g.add_edge(START, "parse_request")
    g.add_edge("parse_request", "check_required_fields")
    g.add_conditional_edges(
        "check_required_fields",
        _should_clarify,
        {"clarify": "generate_response", "retrieve": "retrieve_candidates"},
    )
    g.add_edge("retrieve_candidates", "fetch_weather")
    g.add_edge("fetch_weather", "fetch_live_information")
    g.add_edge("fetch_live_information", "rank_candidates")
    g.add_edge("rank_candidates", "build_itinerary")
    g.add_edge("build_itinerary", "validate_itinerary")
    g.add_conditional_edges(
        "validate_itinerary",
        _should_revise,
        {"revise": "revise_itinerary", "respond": "generate_response"},
    )
    g.add_edge("revise_itinerary", "validate_itinerary")
    g.add_edge("generate_response", END)
    return g.compile()


def build_mock_deps(
    settings: Settings | None = None,
    forecasts: dict[str, dict] | None = None,
    pre_ingest: bool = True,
) -> GraphDeps:
    """构造全 Mock 依赖，测试/离线演示均不依赖网络与数据库。"""
    settings = settings or get_settings()
    embedding = MockEmbedding(dim=settings.vector_dim)
    store = MockVectorStore()
    if pre_ingest:
        ingest_attractions(store, embedding)
    retriever = Retriever(store, embedding)

    return GraphDeps(
        settings=settings,
        amap=AMapTool(mock=True),
        weather=MockWeatherProvider(forecasts=forecasts, today=date.today()),
        tickets=get_ticket_provider(mock=True),
        retriever=retriever,
        validator=ItineraryValidator(
            max_one_way_drive_minutes=settings.max_one_way_drive_minutes,
            default_latest_return=settings.default_latest_return,
            lunch_minutes=settings.lunch_minutes,
        ),
        llm=None,
    )
