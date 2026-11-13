"""Agent 相关的结构化输出模型（供 LLM 使用）。

领域共享模型见 app/schemas.py；此处仅放置 LLM 结构化输出。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field


class ParsedRequest(BaseModel):
    """LLM 从自然语言中解析出的结构化字段（缺失为 None）。"""

    departure: str | None = None
    travel_date: date | None = None
    people_count: int | None = Field(default=None, ge=1, le=50)
    budget: Decimal | None = Field(default=None, gt=0)
    interests: list[str] = Field(default_factory=list)
    physical_level: int = Field(default=3, ge=1, le=5)
    has_elderly: bool = False
    has_children: bool = False
    latest_return_time: str | None = None


class RankedCandidate(BaseModel):
    name: str
    score: int = 0
    reason: str = ""


class RankingResult(BaseModel):
    candidates: list[RankedCandidate] = Field(default_factory=list)


class Revision(BaseModel):
    changes: list[str] = Field(default_factory=list)
