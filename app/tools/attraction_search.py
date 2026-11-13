"""景点检索工具。

从本地 knowledge base（data/attractions.json）及数据库检索景点，依据兴趣、区域、
体力等级与天气条件过滤。实时 POI/票价/路线信息在后续工具补齐。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

from app.schemas import Attraction
from app.services.relevance import relevance_score

_DATA_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "attractions.json"


@lru_cache
def load_attractions() -> list[Attraction]:
    """加载景点种子数据。字段缺失一律为 None，绝不编造价格/坐标/开放时间。"""
    if not _DATA_PATH.exists():
        return []
    raw = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
    return [Attraction(**item) for item in raw]


class AttractionSearchInput(BaseModel):
    interests: list[str] = Field(default_factory=list)
    district: str | None = None
    max_physical_level: int | None = Field(default=None, ge=1, le=5)
    min_indoor_ratio: float | None = Field(default=None, ge=0.0, le=1.0)
    require_elderly_friendly: bool = False
    require_children_friendly: bool = False
    season: str | None = None
    include_tags: list[str] = Field(default_factory=list)
    top_k: int = Field(default=10, ge=1)


def normalize_season(season: str) -> str:
    mapping = {
        "spring": "春季", "summer": "夏季", "autumn": "秋季", "fall": "秋季", "winter": "冬季",
        "春": "春季", "夏": "夏季", "秋": "秋季", "冬": "冬季",
    }
    return mapping.get(season.strip().lower(), season.strip())


def _match_score(attr: Attraction, query: list[str]) -> int:
    """关键词匹配打分（含同义词扩展），用于初筛排序。"""
    return int(relevance_score(attr, query))


def search_attractions(inp: AttractionSearchInput) -> list[Attraction]:
    """按条件筛选景点，返回去重后的列表。

    指定兴趣时**必须做相关性过滤**，不能只排序：否则零相关景点会填满 top_k，
    最终被规划器选中（例如「自然风光」混入正定古城）。
    """
    results: list[Attraction] = []
    season = normalize_season(inp.season) if inp.season else None
    query_words = inp.interests + inp.include_tags

    for attr in load_attractions():
        if inp.district and inp.district not in attr.district:
            continue
        if inp.max_physical_level is not None and attr.physical_level > inp.max_physical_level:
            continue
        if inp.min_indoor_ratio is not None and attr.indoor_ratio < inp.min_indoor_ratio:
            continue
        if inp.require_elderly_friendly and not attr.suitable_for_elderly:
            continue
        if inp.require_children_friendly and not attr.suitable_for_children:
            continue
        if season and attr.suitable_seasons and season not in attr.suitable_seasons:
            continue
        results.append(attr)

    # 兴趣/标签相关度排序；有明确兴趣时剔除零相关条目
    scored = [(a, _match_score(a, query_words)) for a in results]
    if query_words:
        scored = [(a, s) for a, s in scored if s > 0]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    results = [a for a, _ in scored]

    if inp.top_k > 0:
        results = results[: inp.top_k]
    return results
