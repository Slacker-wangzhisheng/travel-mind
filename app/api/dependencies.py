"""FastAPI 依赖注入：构造 Agent 运行所需依赖与图单例。"""
from __future__ import annotations

from functools import lru_cache

from app.agent.graph import GraphDeps, build_graph, build_mock_deps
from app.agent.llm import LLM
from app.config import get_settings
from app.rag.embeddings import build_embedding_provider
from app.rag.retriever import PGVectorStore, Retriever
from app.services.validator import ItineraryValidator
from app.tools.amap import AMapTool
from app.tools.tickets import get_ticket_provider
from app.tools.weather import QWeatherProvider


def _build_live_deps(settings) -> GraphDeps:
    embedding = build_embedding_provider(settings)
    retriever = Retriever(PGVectorStore(), embedding)
    return GraphDeps(
        settings=settings,
        amap=AMapTool(api_key=settings.amap_api_key, mock=False),
        weather=QWeatherProvider(
            api_key=settings.qweather_api_key, host=settings.qweather_api_host
        ),
        tickets=get_ticket_provider(mock=False),
        retriever=retriever,
        validator=ItineraryValidator(
            max_one_way_drive_minutes=settings.max_one_way_drive_minutes,
            default_latest_return=settings.default_latest_return,
            lunch_minutes=settings.lunch_minutes,
        ),
        llm=LLM(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            model=settings.llm_model,
        ),
    )


@lru_cache
def get_deps() -> GraphDeps:
    settings = get_settings()
    if settings.use_mock:
        return build_mock_deps(settings)
    return _build_live_deps(settings)


@lru_cache
def get_graph():
    settings = get_settings()
    if settings.multi_agent_enabled:
        from app.agent.multi_agent_graph import build_multi_agent_graph

        return build_multi_agent_graph(get_deps())
    # MULTI_AGENT_ENABLED=false 时回退到原单 Agent 图（保留回归对比）
    return build_graph(get_deps())


@lru_cache
def get_chat_service():
    from app.services.chat import ChatService
    from app.services.storage import get_store

    return ChatService(deps=get_deps(), graph=get_graph(), store=get_store())


def get_store_dep():
    """FastAPI 依赖：返回持久化存储（可在测试中覆盖）。"""
    from app.services.storage import get_store

    return get_store()


def get_chat_service_dep():
    """FastAPI 依赖：返回对话服务（可在测试中覆盖）。"""
    return get_chat_service()
