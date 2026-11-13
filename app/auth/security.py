"""认证基础：密码哈希与签名 Cookie Token（仅使用标准库）。

- 密码使用 PBKDF2-HMAC-SHA256 + 随机盐，存储格式 pbkdf2_sha256$<iters>$<salt>$<hash>。
- 会话 Token 为 HMAC-SHA256 签名的 payload（user_id + 过期时间），放在 HttpOnly Cookie。
- 不在日志中输出密码、Token 或密钥。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

_ALGORITHM = "pbkdf2_sha256"
_ITERATIONS = 260_000


def hash_password(password: str) -> str:
    if not password or len(password) < 8:
        raise ValueError("密码至少 8 位")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return f"{_ALGORITHM}${_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt_hex, digest_hex = stored.split("$")
        if algorithm != _ALGORITHM:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations)
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _b64decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def sign_token(payload: dict[str, Any], secret: str) -> str:
    body = _b64encode(json.dumps(payload, separators=(",", ":")).encode())
    signature = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
    return f"{body}.{_b64encode(signature)}"


def verify_token(token: str, secret: str, max_age_seconds: int) -> dict[str, Any] | None:
    """校验签名与过期时间；任何异常一律返回 None。"""
    try:
        body, signature = token.split(".", 1)
        expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64decode(signature)):
            return None
        data = json.loads(_b64decode(body))
        issued_at = int(data.get("iat", 0))
        if issued_at <= 0 or time.time() - issued_at > max_age_seconds:
            return None
        return data
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def make_session_token(user_id: str, secret: str) -> str:
    return sign_token({"sub": user_id, "iat": int(time.time())}, secret)


def new_id() -> str:
    return secrets.token_hex(16)
