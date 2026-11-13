"""认证接口：注册 / 登录 / 退出 / 当前用户。

安全要点：
- 密码只存 PBKDF2-HMAC-SHA256 哈希，绝不存明文；
- 会话凭据放在 HttpOnly Cookie（SameSite=Lax，可选 Secure），前端 JS 无法读取；
- 不返回任何密码哈希或 Token；
- 跨站请求风险由 SameSite + 限定 CORS Origin + JSON 请求体共同缓解。
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.api.dependencies import get_store_dep
from app.auth.security import hash_password, make_session_token, verify_password, verify_token
from app.config import get_settings
from app.schemas import LoginRequest, RegisterRequest, UserPublic
from app.services.storage import DuplicateUserError, Store, UserRecord

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _to_public(user: UserRecord) -> UserPublic:
    return UserPublic(id=user.id, email=user.email, display_name=user.display_name)


def current_user_optional(
    request: Request, store: Store = Depends(get_store_dep)
) -> UserRecord | None:
    settings = get_settings()
    token = request.cookies.get(settings.cookie_name)
    if not token:
        return None
    data = verify_token(token, settings.auth_secret, settings.session_ttl_seconds)
    if not data:
        return None
    return store.get_user(str(data.get("sub") or ""))


def require_user(user: UserRecord | None = Depends(current_user_optional)) -> UserRecord:
    if user is None:
        raise HTTPException(status_code=401, detail="未登录或登录已过期")
    return user


def _set_session_cookie(response: Response, user_id: str) -> None:
    settings = get_settings()
    token = make_session_token(user_id, settings.auth_secret)
    response.set_cookie(
        key=settings.cookie_name,
        value=token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        path="/",
    )


@router.post("/register", response_model=UserPublic)
def register(
    req: RegisterRequest, response: Response, store: Store = Depends(get_store_dep)
) -> UserPublic:
    email = (req.email or "").strip().lower()
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="邮箱格式不正确")
    if len(req.password or "") < 8:
        raise HTTPException(status_code=400, detail="密码至少 8 位")
    try:
        user = store.create_user(email, hash_password(req.password), req.display_name)
    except DuplicateUserError as exc:
        raise HTTPException(status_code=409, detail="该邮箱已注册") from exc
    _set_session_cookie(response, user.id)
    return _to_public(user)


@router.post("/login", response_model=UserPublic)
def login(
    req: LoginRequest, response: Response, store: Store = Depends(get_store_dep)
) -> UserPublic:
    user = store.get_user_by_email((req.email or "").strip().lower())
    if user is None or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="邮箱或密码错误")
    _set_session_cookie(response, user.id)
    return _to_public(user)


@router.post("/logout")
def logout(response: Response) -> dict:
    settings = get_settings()
    response.delete_cookie(settings.cookie_name, path="/")
    return {"ok": True}


@router.get("/me", response_model=UserPublic)
def me(user: UserRecord = Depends(require_user)) -> UserPublic:
    return _to_public(user)
