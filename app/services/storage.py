"""持久化存储层：PostgreSQL 实现 + 内存实现（测试与降级）。

安全约定：所有会话 / 消息 / 行程 / 长期记忆的读写都必须同时带 `user_id`，
SQL 条件中包含 user_id，杜绝「仅凭前端传来的 session_id」越权访问。
"""
from __future__ import annotations

import copy
import json
import logging
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from functools import lru_cache
from typing import Any

from app.utils.redact import redact

logger = logging.getLogger(__name__)


class DuplicateUserError(Exception):
    """邮箱已注册。"""


class Store(ABC):
    # ---- 用户 ----
    @abstractmethod
    def create_user(self, email: str, password_hash: str, display_name: str | None = None) -> UserRecord: ...

    @abstractmethod
    def get_user_by_email(self, email: str) -> UserRecord | None: ...

    @abstractmethod
    def get_user(self, user_id: str) -> UserRecord | None: ...

    # ---- 会话 ----
    @abstractmethod
    def create_session(self, user_id: str, title: str, state: dict) -> SessionRecord: ...

    @abstractmethod
    def get_session(self, session_id: str, user_id: str) -> SessionRecord | None: ...

    @abstractmethod
    def list_sessions(self, user_id: str) -> list[SessionRecord]: ...

    @abstractmethod
    def update_session(self, session_id: str, user_id: str, **fields: Any) -> SessionRecord | None: ...

    @abstractmethod
    def delete_session(self, session_id: str, user_id: str) -> bool: ...

    # ---- 消息 ----
    @abstractmethod
    def append_message(
        self, session_id: str, user_id: str, role: str, content: str, meta: dict | None = None
    ) -> MessageRecord: ...

    @abstractmethod
    def list_messages(
        self, session_id: str, user_id: str, limit: int | None = None
    ) -> list[MessageRecord]: ...

    @abstractmethod
    def count_messages(self, session_id: str, user_id: str) -> int: ...

    # ---- 行程 ----
    @abstractmethod
    def save_plan(
        self,
        plan_id: str,
        user_id: str,
        request: dict,
        response: dict,
        session_id: str | None = None,
    ) -> None: ...

    @abstractmethod
    def get_plan(self, plan_id: str, user_id: str) -> SavedPlanRecord | None: ...

    # ---- 长期记忆 ----
    @abstractmethod
    def create_memory(
        self,
        user_id: str,
        type: str,
        content: str,
        source_session_id: str | None = None,
        status: str = "pending",
    ) -> MemoryRecord: ...

    @abstractmethod
    def list_memories(self, user_id: str, status: str | None = None) -> list[MemoryRecord]: ...

    @abstractmethod
    def get_memory(self, memory_id: str, user_id: str) -> MemoryRecord | None: ...

    @abstractmethod
    def update_memory(self, memory_id: str, user_id: str, **fields: Any) -> MemoryRecord | None: ...

    @abstractmethod
    def delete_memory(self, memory_id: str, user_id: str) -> bool: ...


# ---------------------------------------------------------------------------
# 传输对象
# ---------------------------------------------------------------------------


@dataclass
class UserRecord:
    id: str
    email: str
    password_hash: str
    display_name: str | None = None
    created_at: datetime | None = None


@dataclass
class SessionRecord:
    id: str
    user_id: str
    title: str = "新的行程"
    state: dict = field(default_factory=dict)
    summary: str | None = None
    summary_updated_at: datetime | None = None
    last_plan: dict | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class MessageRecord:
    id: Any
    session_id: str
    user_id: str
    role: str
    content: str
    meta: dict | None = None
    created_at: datetime | None = None


@dataclass
class SavedPlanRecord:
    id: str
    user_id: str
    request: dict
    response: dict
    session_id: str | None = None
    created_at: datetime | None = None


@dataclass
class MemoryRecord:
    id: str
    user_id: str
    type: str
    content: str
    status: str = "pending"
    source_session_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


def _json_safe(value: Any) -> Any:
    """把 Decimal / date / datetime / Pydantic 模型等转换为 JSON 可存储结构。"""

    def _default(obj: Any) -> Any:
        if isinstance(obj, Decimal):
            return str(obj)
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        if hasattr(obj, "model_dump"):
            return obj.model_dump(mode="json")
        return str(obj)

    if value is None:
        return None
    return json.loads(json.dumps(value, default=_default, ensure_ascii=False))


# ---------------------------------------------------------------------------
# 内存实现
# ---------------------------------------------------------------------------


class MemoryStore(Store):
    """进程内存储：仅用于测试与数据库不可用时的降级。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._users: dict[str, UserRecord] = {}
        self._by_email: dict[str, str] = {}
        self._sessions: dict[str, SessionRecord] = {}
        self._messages: dict[str, list[MessageRecord]] = {}
        self._plans: dict[str, SavedPlanRecord] = {}
        self._memories: dict[str, MemoryRecord] = {}
        self._seq = 0

    # ---- 用户 ----
    def create_user(self, email: str, password_hash: str, display_name: str | None = None) -> UserRecord:
        with self._lock:
            email = email.lower()
            if email in self._by_email:
                raise DuplicateUserError(email)
            record = UserRecord(
                id=_new_id(),
                email=email,
                password_hash=password_hash,
                display_name=display_name,
                created_at=datetime.now(),
            )
            self._users[record.id] = record
            self._by_email[email] = record.id
            return copy.deepcopy(record)

    def get_user_by_email(self, email: str) -> UserRecord | None:
        uid = self._by_email.get(email.lower())
        return copy.deepcopy(self._users.get(uid)) if uid else None

    def get_user(self, user_id: str) -> UserRecord | None:
        record = self._users.get(user_id)
        return copy.deepcopy(record) if record else None

    # ---- 会话 ----
    def create_session(self, user_id: str, title: str, state: dict) -> SessionRecord:
        now = datetime.now()
        record = SessionRecord(
            id=_new_id(),
            user_id=user_id,
            title=title or "新的行程",
            state=_json_safe(state or {}),
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._sessions[record.id] = record
            self._messages[record.id] = []
        return copy.deepcopy(record)

    def get_session(self, session_id: str, user_id: str) -> SessionRecord | None:
        record = self._sessions.get(session_id)
        if record is None or record.user_id != user_id:
            return None
        return copy.deepcopy(record)

    def list_sessions(self, user_id: str) -> list[SessionRecord]:
        items = [s for s in self._sessions.values() if s.user_id == user_id]
        items.sort(key=lambda s: s.updated_at or datetime.min, reverse=True)
        return [copy.deepcopy(s) for s in items]

    def update_session(self, session_id: str, user_id: str, **fields: Any) -> SessionRecord | None:
        with self._lock:
            record = self._sessions.get(session_id)
            if record is None or record.user_id != user_id:
                return None
            for key in ("state", "summary", "last_plan", "title"):
                if key in fields:
                    value = _json_safe(fields[key]) if key in ("state", "last_plan") else fields[key]
                    setattr(record, key, value)
            if "summary" in fields:
                record.summary_updated_at = datetime.now()
            record.updated_at = datetime.now()
            return copy.deepcopy(record)

    def delete_session(self, session_id: str, user_id: str) -> bool:
        with self._lock:
            record = self._sessions.get(session_id)
            if record is None or record.user_id != user_id:
                return False
            del self._sessions[session_id]
            self._messages.pop(session_id, None)
            return True

    # ---- 消息 ----
    def append_message(
        self, session_id: str, user_id: str, role: str, content: str, meta: dict | None = None
    ) -> MessageRecord:
        with self._lock:
            self._seq += 1
            record = MessageRecord(
                id=self._seq,
                session_id=session_id,
                user_id=user_id,
                role=role,
                content=content,
                meta=_json_safe(meta),
                created_at=datetime.now(),
            )
            self._messages.setdefault(session_id, []).append(record)
            return copy.deepcopy(record)

    def list_messages(
        self, session_id: str, user_id: str, limit: int | None = None
    ) -> list[MessageRecord]:
        session = self._sessions.get(session_id)
        if session is None or session.user_id != user_id:
            return []
        items = list(self._messages.get(session_id, []))
        if limit:
            items = items[-limit:]
        return [copy.deepcopy(m) for m in items]

    def count_messages(self, session_id: str, user_id: str) -> int:
        session = self._sessions.get(session_id)
        if session is None or session.user_id != user_id:
            return 0
        return len(self._messages.get(session_id, []))

    # ---- 行程 ----
    def save_plan(
        self,
        plan_id: str,
        user_id: str,
        request: dict,
        response: dict,
        session_id: str | None = None,
    ) -> None:
        with self._lock:
            self._plans[plan_id] = SavedPlanRecord(
                id=plan_id,
                user_id=user_id,
                request=_json_safe(request),
                response=_json_safe(response),
                session_id=session_id,
                created_at=datetime.now(),
            )

    def get_plan(self, plan_id: str, user_id: str) -> SavedPlanRecord | None:
        record = self._plans.get(plan_id)
        if record is None or record.user_id != user_id:
            return None
        return copy.deepcopy(record)

    # ---- 长期记忆 ----
    def create_memory(
        self,
        user_id: str,
        type: str,
        content: str,
        source_session_id: str | None = None,
        status: str = "pending",
    ) -> MemoryRecord:
        now = datetime.now()
        record = MemoryRecord(
            id=_new_id(),
            user_id=user_id,
            type=type,
            content=content,
            status=status,
            source_session_id=source_session_id,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._memories[record.id] = record
        return copy.deepcopy(record)

    def list_memories(self, user_id: str, status: str | None = None) -> list[MemoryRecord]:
        items = [
            m
            for m in self._memories.values()
            if m.user_id == user_id and (status is None or m.status == status)
        ]
        items.sort(key=lambda m: m.created_at or datetime.min)
        return [copy.deepcopy(m) for m in items]

    def get_memory(self, memory_id: str, user_id: str) -> MemoryRecord | None:
        record = self._memories.get(memory_id)
        if record is None or record.user_id != user_id:
            return None
        return copy.deepcopy(record)

    def update_memory(self, memory_id: str, user_id: str, **fields: Any) -> MemoryRecord | None:
        with self._lock:
            record = self._memories.get(memory_id)
            if record is None or record.user_id != user_id:
                return None
            for key in ("type", "content", "status"):
                if key in fields and fields[key] is not None:
                    setattr(record, key, fields[key])
            record.updated_at = datetime.now()
            return copy.deepcopy(record)

    def delete_memory(self, memory_id: str, user_id: str) -> bool:
        with self._lock:
            record = self._memories.get(memory_id)
            if record is None or record.user_id != user_id:
                return False
            del self._memories[memory_id]
            return True


# ---------------------------------------------------------------------------
# PostgreSQL 实现
# ---------------------------------------------------------------------------


class SqlAlchemyStore(Store):
    """PostgreSQL 持久化实现；所有查询都带 user_id 条件。"""

    def _session(self):
        from app.db.session import get_session

        return get_session()

    # ---- 用户 ----
    def create_user(self, email: str, password_hash: str, display_name: str | None = None) -> UserRecord:
        from sqlalchemy import select

        from app.db.models import User

        email = email.lower()
        with self._session() as session:
            if session.scalar(select(User).where(User.email == email)) is not None:
                raise DuplicateUserError(email)
            row = User(
                id=_new_id(), email=email, password_hash=password_hash, display_name=display_name
            )
            session.add(row)
            session.commit()
            return _to_user(row)

    def get_user_by_email(self, email: str) -> UserRecord | None:
        from sqlalchemy import select

        from app.db.models import User

        with self._session() as session:
            row = session.scalar(select(User).where(User.email == email.lower()))
            return _to_user(row) if row else None

    def get_user(self, user_id: str) -> UserRecord | None:
        from app.db.models import User

        with self._session() as session:
            row = session.get(User, user_id)
            return _to_user(row) if row else None

    # ---- 会话 ----
    def create_session(self, user_id: str, title: str, state: dict) -> SessionRecord:
        from sqlalchemy import func

        from app.db.models import ChatSessionRow

        with self._session() as session:
            row = ChatSessionRow(
                id=_new_id(),
                user_id=user_id,
                title=(title or "新的行程")[:120],
                state=_json_safe(state or {}),
                updated_at=func.now(),
            )
            session.add(row)
            session.commit()
            return _to_session(row)

    def get_session(self, session_id: str, user_id: str) -> SessionRecord | None:
        from app.db.models import ChatSessionRow

        with self._session() as session:
            row = session.get(ChatSessionRow, session_id)
            if row is None or row.user_id != user_id:
                return None
            return _to_session(row)

    def list_sessions(self, user_id: str) -> list[SessionRecord]:
        from sqlalchemy import select

        from app.db.models import ChatSessionRow

        with self._session() as session:
            rows = session.scalars(
                select(ChatSessionRow)
                .where(ChatSessionRow.user_id == user_id)
                .order_by(ChatSessionRow.updated_at.desc())
            ).all()
            return [_to_session(r) for r in rows]

    def update_session(self, session_id: str, user_id: str, **fields: Any) -> SessionRecord | None:
        from sqlalchemy import func

        from app.db.models import ChatSessionRow

        with self._session() as session:
            row = session.get(ChatSessionRow, session_id)
            if row is None or row.user_id != user_id:
                return None
            if "state" in fields:
                row.state = _json_safe(fields["state"])
            if "last_plan" in fields:
                row.last_plan = _json_safe(fields["last_plan"])
            if "summary" in fields:
                row.summary = fields["summary"]
                row.summary_updated_at = func.now()
            if "title" in fields and fields["title"]:
                row.title = str(fields["title"])[:120]
            row.updated_at = func.now()
            session.commit()
            return _to_session(row)

    def delete_session(self, session_id: str, user_id: str) -> bool:
        from sqlalchemy import delete

        from app.db.models import ChatMessageRow, ChatSessionRow

        with self._session() as session:
            row = session.get(ChatSessionRow, session_id)
            if row is None or row.user_id != user_id:
                return False
            session.execute(
                delete(ChatMessageRow).where(
                    ChatMessageRow.session_id == session_id, ChatMessageRow.user_id == user_id
                )
            )
            session.delete(row)
            session.commit()
            return True

    # ---- 消息 ----
    def append_message(
        self, session_id: str, user_id: str, role: str, content: str, meta: dict | None = None
    ) -> MessageRecord:
        from app.db.models import ChatMessageRow

        with self._session() as session:
            row = ChatMessageRow(
                session_id=session_id,
                user_id=user_id,
                role=role,
                content=content,
                meta=_json_safe(meta),
            )
            session.add(row)
            session.commit()
            return _to_message(row)

    def list_messages(
        self, session_id: str, user_id: str, limit: int | None = None
    ) -> list[MessageRecord]:
        from sqlalchemy import select

        from app.db.models import ChatMessageRow

        with self._session() as session:
            if limit:
                rows = session.scalars(
                    select(ChatMessageRow)
                    .where(
                        ChatMessageRow.session_id == session_id,
                        ChatMessageRow.user_id == user_id,
                    )
                    .order_by(ChatMessageRow.id.desc())
                    .limit(limit)
                ).all()
                rows = list(reversed(rows))
            else:
                rows = session.scalars(
                    select(ChatMessageRow)
                    .where(
                        ChatMessageRow.session_id == session_id,
                        ChatMessageRow.user_id == user_id,
                    )
                    .order_by(ChatMessageRow.id.asc())
                ).all()
            return [_to_message(r) for r in rows]

    def count_messages(self, session_id: str, user_id: str) -> int:
        from sqlalchemy import func, select

        from app.db.models import ChatMessageRow

        with self._session() as session:
            return int(
                session.scalar(
                    select(func.count())
                    .select_from(ChatMessageRow)
                    .where(
                        ChatMessageRow.session_id == session_id,
                        ChatMessageRow.user_id == user_id,
                    )
                )
                or 0
            )

    # ---- 行程 ----
    def save_plan(
        self,
        plan_id: str,
        user_id: str,
        request: dict,
        response: dict,
        session_id: str | None = None,
    ) -> None:
        from app.db.models import SavedPlan

        with self._session() as session:
            session.add(
                SavedPlan(
                    id=plan_id,
                    user_id=user_id,
                    session_id=session_id,
                    request=_json_safe(request),
                    response=_json_safe(response),
                )
            )
            session.commit()

    def get_plan(self, plan_id: str, user_id: str) -> SavedPlanRecord | None:
        from app.db.models import SavedPlan

        with self._session() as session:
            row = session.get(SavedPlan, plan_id)
            if row is None or row.user_id != user_id:
                return None
            return SavedPlanRecord(
                id=row.id,
                user_id=row.user_id,
                request=row.request,
                response=row.response,
                session_id=row.session_id,
                created_at=row.created_at,
            )

    # ---- 长期记忆 ----
    def create_memory(
        self,
        user_id: str,
        type: str,
        content: str,
        source_session_id: str | None = None,
        status: str = "pending",
    ) -> MemoryRecord:
        from sqlalchemy import func

        from app.db.models import UserMemory

        with self._session() as session:
            row = UserMemory(
                id=_new_id(),
                user_id=user_id,
                type=type,
                content=content,
                source_session_id=source_session_id,
                status=status,
                updated_at=func.now(),
            )
            session.add(row)
            session.commit()
            return _to_memory(row)

    def list_memories(self, user_id: str, status: str | None = None) -> list[MemoryRecord]:
        from sqlalchemy import select

        from app.db.models import UserMemory

        with self._session() as session:
            stmt = select(UserMemory).where(UserMemory.user_id == user_id)
            if status is not None:
                stmt = stmt.where(UserMemory.status == status)
            rows = session.scalars(stmt.order_by(UserMemory.created_at.asc())).all()
            return [_to_memory(r) for r in rows]

    def get_memory(self, memory_id: str, user_id: str) -> MemoryRecord | None:
        from app.db.models import UserMemory

        with self._session() as session:
            row = session.get(UserMemory, memory_id)
            if row is None or row.user_id != user_id:
                return None
            return _to_memory(row)

    def update_memory(self, memory_id: str, user_id: str, **fields: Any) -> MemoryRecord | None:
        from sqlalchemy import func

        from app.db.models import UserMemory

        with self._session() as session:
            row = session.get(UserMemory, memory_id)
            if row is None or row.user_id != user_id:
                return None
            for key in ("type", "content", "status"):
                if key in fields and fields[key] is not None:
                    setattr(row, key, fields[key])
            row.updated_at = func.now()
            session.commit()
            return _to_memory(row)

    def delete_memory(self, memory_id: str, user_id: str) -> bool:
        from app.db.models import UserMemory

        with self._session() as session:
            row = session.get(UserMemory, memory_id)
            if row is None or row.user_id != user_id:
                return False
            session.delete(row)
            session.commit()
            return True


# ---------------------------------------------------------------------------
# 转换
# ---------------------------------------------------------------------------


def _to_user(row: Any) -> UserRecord:
    return UserRecord(
        id=row.id,
        email=row.email,
        password_hash=row.password_hash,
        display_name=row.display_name,
        created_at=row.created_at,
    )


def _to_session(row: Any) -> SessionRecord:
    return SessionRecord(
        id=row.id,
        user_id=row.user_id,
        title=row.title,
        state=row.state or {},
        summary=row.summary,
        summary_updated_at=row.summary_updated_at,
        last_plan=row.last_plan,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_message(row: Any) -> MessageRecord:
    return MessageRecord(
        id=row.id,
        session_id=row.session_id,
        user_id=row.user_id,
        role=row.role,
        content=row.content,
        meta=row.meta,
        created_at=row.created_at,
    )


def _to_memory(row: Any) -> MemoryRecord:
    return MemoryRecord(
        id=row.id,
        user_id=row.user_id,
        type=row.type,
        content=row.content,
        status=row.status,
        source_session_id=row.source_session_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _new_id() -> str:
    from uuid import uuid4

    return str(uuid4())


# ---------------------------------------------------------------------------
# 选择实现
# ---------------------------------------------------------------------------


def ensure_schema() -> None:
    """创建本阶段新增表（不动 document_chunks，避免 vector 扩展权限问题）。"""
    import app.db.models as models
    from app.db.base import Base
    from app.db.session import get_engine

    tables = [
        models.User.__table__,
        models.ChatSessionRow.__table__,
        models.ChatMessageRow.__table__,
        models.SavedPlan.__table__,
        models.UserMemory.__table__,
    ]
    Base.metadata.create_all(get_engine(), tables=tables)


@lru_cache
def get_store() -> Store:
    from app.config import get_settings

    settings = get_settings()
    backend = (settings.storage_backend or "auto").lower()
    if backend == "memory":
        logger.info("存储后端：内存（仅测试/降级）")
        return MemoryStore()

    try:
        if settings.auto_create_tables:
            ensure_schema()
        logger.info("存储后端：PostgreSQL")
        return SqlAlchemyStore()
    except Exception as exc:  # noqa: BLE001 - 数据库不可用不应阻断启动
        if backend == "db":
            raise
        logger.warning(
            "数据库不可用，降级为内存存储（重启/多进程不保证持久化）：%s", redact(exc)
        )
        return MemoryStore()


def reset_store_cache() -> None:
    get_store.cache_clear()
