"""FastAPI 路由。"""
from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException

from app.api.auth import current_user_optional, require_user
from app.api.dependencies import get_chat_service_dep, get_deps, get_graph, get_store_dep
from app.rag.ingest import ingest_attractions
from app.schemas import (
    ChatRequest,
    ChatResponse,
    ItineraryOption,
    PlanRequest,
    PlanResponse,
    SessionDetail,
    SessionSummary,
)
from app.services.chat import SessionAccessError
from app.services.storage import Store, UserRecord
from app.tools.attraction_search import load_attractions

router = APIRouter()

# 未登录用户的临时行程（进程内）；登录用户的行程写入 saved_plans。
_PLANS: dict[str, PlanResponse] = {}


def _state_from_request(req: PlanRequest) -> dict:
    return {
        "departure": req.departure,
        "travel_date": req.travel_date,
        "people_count": req.people_count,
        "budget": req.budget,
        "interests": req.interests,
        "transport_mode": req.transport_mode,
        "physical_level": req.physical_level,
        "has_elderly": req.has_elderly,
        "has_children": req.has_children,
        "latest_return_time": req.latest_return_time,
        "start_time": req.start_time,
        "exclude_attractions": req.exclude_attractions,
        "exclude_districts": req.exclude_districts,
        "max_one_way_drive_km": req.max_one_way_drive_km,
    }


@router.get("/health")
def health() -> dict:
    from app.agent import prompts
    from app.config import get_settings

    return {
        "status": "ok",
        "service": "travel-mind",
        "prompt_version": prompts.PROMPT_VERSION,
        "multi_agent": get_settings().multi_agent_enabled,
    }


@router.post("/api/v1/plan", response_model=PlanResponse)
def create_plan(
    req: PlanRequest,
    user: UserRecord | None = Depends(current_user_optional),
    store: Store = Depends(get_store_dep),
) -> PlanResponse:
    result = get_graph().invoke(_state_from_request(req))
    resp: PlanResponse = result["response"]
    # 与多 Agent 出口保持同一门禁：旧图也绝不能返回违反硬约束的方案
    from app.services.feasibility import filter_feasible

    outcome = filter_feasible(resp.itinerary_options, req, get_deps().validator)
    resp.itinerary_options = outcome.options
    if not resp.itinerary_options:
        resp.explanation = (
            outcome.message or resp.explanation or "没有找到满足硬约束的行程方案，请调整条件后重试。"
        )
    plan_id = str(uuid4())
    resp.plan_id = plan_id
    if user is not None:
        store.save_plan(
            plan_id,
            user.id,
            req.model_dump(mode="json"),
            resp.model_dump(mode="json"),
        )
    else:
        _PLANS[plan_id] = resp
    return resp


@router.post("/api/v1/chat", response_model=ChatResponse)
def chat(
    req: ChatRequest,
    user: UserRecord | None = Depends(current_user_optional),
    service=Depends(get_chat_service_dep),
) -> ChatResponse:
    try:
        return service.handle(
            req.message, req.session_id, req.form, user_id=user.id if user else None
        )
    except SessionAccessError as exc:
        # 不区分「不存在」与「不属于你」，避免泄露他人会话是否存在
        raise HTTPException(status_code=404, detail="会话不存在") from exc


@router.get("/api/v1/chat/sessions", response_model=list[SessionSummary])
def list_sessions(
    user: UserRecord = Depends(require_user), store: Store = Depends(get_store_dep)
) -> list[SessionSummary]:
    return [
        SessionSummary(
            id=s.id, title=s.title, updated_at=s.updated_at, created_at=s.created_at
        )
        for s in store.list_sessions(user.id)
    ]


@router.get("/api/v1/chat/sessions/{session_id}", response_model=SessionDetail)
def get_session(
    session_id: str,
    user: UserRecord = Depends(require_user),
    store: Store = Depends(get_store_dep),
) -> SessionDetail:
    record = store.get_session(session_id, user.id)
    if record is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    messages = [
        {"role": m.role, "content": m.content} for m in store.list_messages(session_id, user.id)
    ]
    options: list[ItineraryOption] = []
    last_plan = (record.last_plan or {}).get("itinerary_options") or []
    for item in last_plan:
        try:
            options.append(ItineraryOption.model_validate(item))
        except Exception:  # noqa: BLE001 - 老旧数据损坏时忽略该方案
            continue
    return SessionDetail(
        session=SessionSummary(
            id=record.id,
            title=record.title,
            updated_at=record.updated_at,
            created_at=record.created_at,
        ),
        messages=messages,
        itinerary_options=options,
        applied_preferences=list((record.state or {}).get("long_term_preferences") or []),
    )


@router.delete("/api/v1/chat/sessions/{session_id}")
def delete_session(
    session_id: str,
    user: UserRecord = Depends(require_user),
    store: Store = Depends(get_store_dep),
) -> dict:
    if not store.delete_session(session_id, user.id):
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"ok": True}


@router.post("/api/v1/knowledge/ingest")
def ingest_knowledge() -> dict:
    deps = get_deps()
    count = ingest_attractions(deps.retriever.store, deps.retriever.embedding)
    return {"ingested_chunks": count}


@router.get("/api/v1/attractions")
def list_attractions() -> dict:
    return {"attractions": [a.model_dump() for a in load_attractions()]}


@router.get("/api/v1/plans/{plan_id}", response_model=PlanResponse)
def get_plan(
    plan_id: str,
    user: UserRecord | None = Depends(current_user_optional),
    store: Store = Depends(get_store_dep),
) -> PlanResponse:
    if user is not None:
        record = store.get_plan(plan_id, user.id)
        if record is not None:
            return PlanResponse.model_validate(record.response)
    plan = _PLANS.get(plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="plan not found")
    return plan
