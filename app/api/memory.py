"""长期记忆接口：查看 / 手动新增 / 修改 / 确认 / 删除。

所有操作都以当前登录用户的 user_id 为条件，用户 A 永远看不到用户 B 的记忆。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.auth import require_user
from app.api.dependencies import get_store_dep
from app.schemas import MemoryCreateRequest, MemorySuggestion, MemoryUpdateRequest
from app.services.storage import Store, UserRecord

router = APIRouter(prefix="/api/v1/memories", tags=["memories"])


def _to_dto(record) -> MemorySuggestion:
    return MemorySuggestion(
        id=record.id, type=record.type, content=record.content, status=record.status
    )


@router.get("", response_model=list[MemorySuggestion])
def list_memories(
    user: UserRecord = Depends(require_user), store: Store = Depends(get_store_dep)
) -> list[MemorySuggestion]:
    return [_to_dto(m) for m in store.list_memories(user.id) if m.status != "deleted"]


@router.post("", response_model=MemorySuggestion)
def create_memory(
    body: MemoryCreateRequest,
    user: UserRecord = Depends(require_user),
    store: Store = Depends(get_store_dep),
) -> MemorySuggestion:
    content = (body.content or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="记忆内容不能为空")
    record = store.create_memory(user.id, body.type or "interest", content, status="confirmed")
    return _to_dto(record)


@router.patch("/{memory_id}", response_model=MemorySuggestion)
def update_memory(
    memory_id: str,
    body: MemoryUpdateRequest,
    user: UserRecord = Depends(require_user),
    store: Store = Depends(get_store_dep),
) -> MemorySuggestion:
    record = store.update_memory(
        memory_id, user.id, content=body.content, status=body.status
    )
    if record is None:
        raise HTTPException(status_code=404, detail="记忆不存在")
    return _to_dto(record)


@router.delete("/{memory_id}")
def delete_memory(
    memory_id: str,
    user: UserRecord = Depends(require_user),
    store: Store = Depends(get_store_dep),
) -> dict:
    if not store.delete_memory(memory_id, user.id):
        raise HTTPException(status_code=404, detail="记忆不存在")
    return {"ok": True}
