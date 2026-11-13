"""日志脱敏：避免把 API Key、Bearer Token、数据库密码写入日志。"""
from __future__ import annotations

import re

_SECRET_QUERY = re.compile(r"((?:key|api_key|apikey|token|access_token|secret)=)[^&\s'\"<>]+", re.I)
_BEARER = re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.I)
_DB_PASSWORD = re.compile(r"(://[^:/@\s]+:)[^@\s]+(@)", re.I)


def redact(text: str) -> str:
    """把 URL 查询参数、Bearer Token、数据库连接串密码替换为 ***。"""
    value = str(text)
    value = _SECRET_QUERY.sub(r"\1***", value)
    value = _BEARER.sub(r"\1***", value)
    value = _DB_PASSWORD.sub(r"\1***\2", value)
    return value
