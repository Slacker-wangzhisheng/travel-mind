"""兴趣相关性：确定性的同义词扩展与打分。

为什么需要：
- 规则检索此前只做「排序」，零相关景点仍会进入候选，最终被规划器选中；
- 这里提供统一的相关性判定，供检索、排序、规划、审查、最终出口共同使用，
  确保「自然风光/山岳/森林」不会混入正定古城这类历史文化景点。
"""
from __future__ import annotations

from typing import Any

# 兴趣 → 匹配词。用于弥补「自然风光」在数据里写作「山水/森林/登山」等同义表达。
INTEREST_SYNONYMS: dict[str, list[str]] = {
    # 注意：不要收录泛化的「风景」，高德把大量 POI 统一标为「风景名胜」，
    # 否则教堂、旧址等也会被误判为自然风光（真实案例：基督教怀恩堂）。
    "自然风光": [
        "自然风光", "自然", "山水", "森林", "山岳", "登山",
        "峡谷", "瀑布", "草甸", "红叶", "地质", "山峰",
    ],
    "自然": ["自然", "山水", "森林", "山岳", "登山", "峡谷", "瀑布", "草甸", "红叶", "地质"],
    # 注意：不要使用单字「山」，否则会误命中「三山不见」这类城市介绍文案
    "山岳": ["山岳", "登山", "山峰", "山地", "山寨", "峰", "寨", "森林", "草甸"],
    "森林": ["森林", "原始森林", "林场", "树木", "草甸"],
    "登山": ["登山", "徒步", "山岳", "山峰", "寨", "步道"],
    "徒步": ["徒步", "登山", "步道", "森林"],
    "历史文化": ["历史文化", "历史", "文化", "古城", "古建筑", "寺", "庙", "塔", "遗址", "府", "宗教", "桥"],
    "古建筑": ["古建筑", "建筑", "寺", "庙", "塔", "桥", "古城", "历史"],
    "宗教": ["宗教", "寺", "庙", "禅", "观", "宗教"],
    "红色旅游": ["红色旅游", "红色", "革命", "旧址", "纪念馆", "爱国主义"],
    "亲子": ["亲子", "儿童", "公园", "乐园", "适合步行", "轻松"],
    "摄影": ["摄影", "红叶", "地质", "风光"],
    "避暑": ["避暑", "清凉", "夏季", "森林", "草甸", "溪流"],
    "地质": ["地质", "地貌", "岩石", "丹霞", "峡谷"],
    "古城": ["古城", "古城墙", "县城", "古建筑"],
    "沙漠": ["沙漠", "沙地", "戈壁"],
}


# 高德批量抓取时给几乎所有景点打的泛化标签，不含任何兴趣信息，参与匹配只会制造噪声。
_GENERIC_TAGS = {"风景名胜", "风景名胜相关", "旅游景点", "景点"}

# 纯自然类兴趣：这类请求下，名称带明显人文标记的 POI 不应仅凭被误标的数据进入方案。
_NATURE_INTERESTS = {"自然风光", "自然", "山岳", "森林", "登山", "徒步", "避暑", "地质"}
_CULTURAL_MARKERS = (
    "旧址", "纪念馆", "故居", "教堂", "纪念堂", "遗址", "寺", "庙",
    "古城", "宫", "祠", "府", "塔", "桥",
)


def interest_terms(interests: list[str]) -> list[str]:
    """把用户兴趣扩展为匹配词列表（去重，保留原词优先）。"""
    terms: list[str] = []
    for item in interests or []:
        text = str(item).strip()
        if not text:
            continue
        if text not in terms:
            terms.append(text)
        for synonym in INTEREST_SYNONYMS.get(text, []):
            if synonym not in terms:
                terms.append(synonym)
    return terms


def _haystack(attr: Any) -> str:
    tags = [
        t for t in (getattr(attr, "tags", []) or [])
        if str(t).strip() not in _GENERIC_TAGS
    ]
    return (
        f"{attr.name} {getattr(attr, 'district', '') or ''} "
        f"{getattr(attr, 'introduction', '') or ''} "
        f"{' '.join(getattr(attr, 'categories', []) or [])} "
        f"{' '.join(tags)}"
    ).lower()


def relevance_score(attr: Any, interests: list[str]) -> float:
    """0 表示与用户兴趣无关；数值越大越相关。"""
    terms = interest_terms(interests)
    if not terms:
        return 1.0
    hay = _haystack(attr)
    name = (getattr(attr, "name", "") or "").lower()
    score = 0.0
    for term in terms:
        tl = term.lower()
        if tl and tl in name:
            score += 5.0
        elif tl and tl in hay:
            score += 2.0
    if score > 0 and _is_cultural_conflict(name, interests):
        return 0.0
    return score


def _is_cultural_conflict(name: str, interests: list[str]) -> bool:
    """纯自然兴趣下，名称含人文标记且自身没有自然词命中的 POI 判为不相关。

    用于兜住数据脏标：例如高德把「中央宣传部旧址」的类别标成「自然风光」。
    """
    if not interests or any(i not in _NATURE_INTERESTS for i in interests):
        return False
    if not any(marker in name for marker in _CULTURAL_MARKERS):
        return False
    return not any(t.lower() in name for t in interest_terms(interests))


def is_relevant(attr: Any, interests: list[str]) -> bool:
    """用户未指定兴趣时不限制；指定时必须至少有 1 个匹配。"""
    if not interests:
        return True
    return relevance_score(attr, interests) > 0


def filter_relevant(attrs: list[Any], interests: list[str]) -> list[Any]:
    return [a for a in attrs if is_relevant(a, interests)]
