"""长期记忆：候选偏好提取、Prompt 注入与 DTO。

原则：
- 只提取跨多次旅行仍有用的稳定偏好（兴趣 / 同行人群 / 体力 / 常用出发区域）；
- 一次性约束（日期、预算、「本次不去某景点」）绝不写入长期记忆；
- LLM 可以提议，但必须用户确认（pending → confirmed）后才参与规划。
"""
from __future__ import annotations

from app.schemas import PlanRequest

_DISTRICTS = [
    "长安区", "桥西区", "新华区", "裕华区", "藁城区", "鹿泉区", "栾城区", "井陉矿区",
    "正定", "赵县", "平山", "井陉", "灵寿", "赞皇", "元氏", "行唐", "无极", "深泽",
]


def propose_preferences(form: PlanRequest | None, state: dict | None = None) -> list[dict]:
    """从本次请求中提取「可长期保存」的偏好候选。返回 [{type, content}]。"""
    proposals: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: str, content: str) -> None:
        key = (kind, content)
        if content and key not in seen:
            seen.add(key)
            proposals.append({"type": kind, "content": content})

    interests = (form.interests if form else None) or (state or {}).get("interests") or []
    for item in interests:
        text = str(item).strip()
        if text:
            add("interest", text)

    has_elderly = form.has_elderly if form else bool((state or {}).get("has_elderly"))
    has_children = form.has_children if form else bool((state or {}).get("has_children"))
    if has_elderly:
        add("companion", "常带老人出行")
    if has_children:
        add("companion", "常带儿童出行")

    physical = form.physical_level if form else (state or {}).get("physical_level")
    if physical is not None and int(physical) <= 2:
        add("physical", "偏好低强度行程")

    departure = (form.departure if form else None) or (state or {}).get("departure")
    if departure:
        for d in _DISTRICTS:
            if d in departure:
                add("departure", f"常从{d}出发")
                break
    return proposals


def memories_prompt_lines(memories: list) -> str:
    """已确认偏好转成 RequirementAgent Prompt 的一行说明。"""
    if not memories:
        return ""
    parts = [f"{m.type}:{m.content}" for m in memories if getattr(m, "content", None)]
    return "；".join(parts)


def preference_interests(memories: list) -> list[str]:
    return [m.content for m in memories if m.type == "interest" and m.content]


def preference_departure(memories: list) -> str | None:
    for m in memories:
        if m.type == "departure" and m.content:
            # "常从裕华区出发" → "裕华区"
            text = m.content.replace("常从", "").replace("出发", "")
            return text or None
    return None


def preference_has_elderly(memories: list) -> bool:
    return any(m.type == "companion" and "老人" in (m.content or "") for m in memories)


def preference_has_children(memories: list) -> bool:
    return any(m.type == "companion" and "儿童" in (m.content or "") for m in memories)


def preference_physical_level(memories: list) -> int | None:
    for m in memories:
        if m.type == "physical" and "低强度" in (m.content or ""):
            return 2
    return None
