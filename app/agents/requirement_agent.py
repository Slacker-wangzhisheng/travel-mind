"""RequirementAgent：解析用户需求、判断完整性、处理增量修改。

- 只做需求理解与结构化，禁止使用向量库 / 天气 / 地图 / 票务 / 行程生成。
- 「想去」区县 (include_districts) 与「不去」区县 (exclude_districts) 严格分开，
  并以内置规则兜底：凡「不去/排除 X」必须落入 exclude_districts 且移出 include_districts。
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from app.agents.base import Agent
from app.agents.schemas import RequirementResult

# 石家庄主要区/县，用于「不去某区域」否定兜底匹配
_DISTRICT_KEYWORDS = [
    "正定县", "正定", "长安区", "桥西区", "新华区", "裕华区", "藁城区",
    "鹿泉区", "栾城区", "井陉矿区", "井陉县", "赵县", "平山县", "平山",
    "灵寿县", "灵寿", "赞皇县", "元氏县", "元氏", "行唐县", "行唐",
    "无极县", "无极", "深泽县", "深泽", "井陉", "鹿泉", "赵县",
]

# LLM 不可用（未配置/欠费/网络故障）时的确定性关键词表
_INTEREST_KEYWORDS = [
    "自然风光", "山岳", "森林", "登山", "徒步", "避暑", "摄影", "地质",
    "历史文化", "古城", "古建筑", "宗教", "红色旅游", "亲子", "沙漠",
]

_REQUIRED = {
    "departure": "出发位置",
    "travel_date": "出行日期",
    "people_count": "出行人数",
    "budget": "预算",
}


def _parse_text_fallback(text: str) -> dict:
    """从自然语言里确定性抽取核心约束（LLM 不可用时的降级路径）。

    只覆盖常见表达，抽取不到的字段保持缺失，继续走追问，绝不猜测预算/人数。
    """
    data: dict = {}
    m = re.search(r"从([\u4e00-\u9fa5]{2,20}?)(?:出发|自驾|开车)", text)
    if m:
        data["departure"] = m.group(1)

    m = re.search(r"(\d+)\s*人", text)
    if m:
        data["people_count"] = int(m.group(1))

    m = re.search(r"预算\s*(\d+(?:\.\d+)?)\s*元", text)
    if m is None:
        m = re.search(r"(\d+(?:\.\d+)?)\s*元", text)
    if m:
        data["budget"] = Decimal(m.group(1))

    m = re.search(r"(\d{1,2})[:：](\d{2})\s*出发", text)
    if m:
        data["start_time"] = f"{int(m.group(1)):02d}:{m.group(2)}"

    m = re.search(r"(?:最晚|最迟)?\s*(\d{1,2})[:：](\d{2})\s*(?:返回|回)", text)
    if m:
        data["latest_return_time"] = f"{int(m.group(1)):02d}:{m.group(2)}"

    interests = [k for k in _INTEREST_KEYWORDS if k in text]
    if interests:
        data["interests"] = interests

    if "明天" in text:
        data["travel_date"] = date.today() + timedelta(days=1)
    elif "后天" in text:
        data["travel_date"] = date.today() + timedelta(days=2)
    elif "今天" in text or "一日游" in text:
        data["travel_date"] = date.today()

    return data


def _detect_negative_districts(text: str) -> list[str]:
    """从文本中找出「不去/排除某区县」的区县名（确定性兜底）。"""
    found: list[str] = []
    if not text:
        return found
    for d in _DISTRICT_KEYWORDS:
        if re.search(rf"(不去|排除|别去|不去逛|不想去)\s*{re.escape(d)}", text):
            if d not in found:
                found.append(d)
    return found


def _strip_keywords(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    return [str(x).strip() for x in value if str(x).strip()]


class RequirementAgent(Agent):
    name = "RequirementAgent"

    def run(self, state: dict, deps: Any) -> dict:
        text = (state.get("user_request") or "").strip()
        req = RequirementResult(
            departure=state.get("departure"),
            travel_date=state.get("travel_date"),
            people_count=state.get("people_count"),
            budget=state.get("budget"),
            interests=list(state.get("interests") or []),
            physical_level=state.get("physical_level") or 3,
            has_elderly=bool(state.get("has_elderly")),
            has_children=bool(state.get("has_children")),
            latest_return_time=state.get("latest_return_time") or "21:00",
            start_time=state.get("start_time") or "08:00",
            include_districts=_strip_keywords(state.get("include_districts")),
            exclude_districts=_strip_keywords(state.get("exclude_districts")),
            exclude_attractions=_strip_keywords(state.get("exclude_attractions")),
            max_one_way_drive_km=state.get("max_one_way_drive_km"),
        )

        llm = getattr(deps, "llm", None)
        if text and llm and getattr(llm, "enabled", False) and not self._specified(state):
            summary = state.get("conversation_summary") or ""
            llm_input = f"{text}\n（历史摘要，仅供参考）{summary}" if summary else text
            parsed = llm.parse_requirement(llm_input)
            self._overlay(req, parsed, state)

        # LLM 未启用/欠费/解析失败时的确定性抽取：不能因为模型不可用就把
        # 已经写清楚的条件当成「未提供」而反复追问用户。
        if text and not self._specified(state):
            self._overlay(req, _parse_text_fallback(text), state)

        # 长期记忆：仅填补本次未明确指定的字段，当前输入永远优先
        self._apply_long_term(req, state.get("long_term_preferences") or [])

        # 否定区县兜底：确保「不去 X」绝不变成 include，也绝不留在 include。
        for d in _detect_negative_districts(text):
            if d not in req.exclude_districts:
                req.exclude_districts.append(d)
            req.include_districts = [x for x in req.include_districts if x != d and d not in x]

        # include/exclude 互斥清理
        self._dedupe_exclusive(req)

        missing = [
            label for key, label in _REQUIRED.items() if getattr(req, key) in (None, "")
        ]
        req.complete = not missing
        req.missing_fields = missing

        out: dict = {
            "requirement_result": req,
            "current_agent": self.name,
            "departure": req.departure,
            "travel_date": req.travel_date,
            "people_count": req.people_count,
            "budget": req.budget,
            "interests": req.interests,
            "physical_level": req.physical_level,
            "has_elderly": req.has_elderly,
            "has_children": req.has_children,
            "latest_return_time": req.latest_return_time,
            "start_time": req.start_time,
            "include_districts": req.include_districts,
            "exclude_districts": req.exclude_districts,
            "exclude_attractions": req.exclude_attractions,
            "max_one_way_drive_km": req.max_one_way_drive_km,
            "missing_fields": missing,
            "clarification_required": not req.complete,
            "clarification_question": "请补充：" + "、".join(missing) if missing else "",
        }
        # 图节点据 complete 决定走追问还是进 Supervisor
        out["requirement_complete"] = req.complete
        out["next_agent"] = "Supervisor" if req.complete else "FinalResponse"
        out["route_reason"] = "需求完整，进入编排" if req.complete else "信息不完整，追问用户"
        return out

    @staticmethod
    def _specified(state: dict) -> bool:
        return all(
            [
                state.get("departure"),
                state.get("travel_date") is not None,
                state.get("people_count"),
                state.get("budget") is not None,
            ]
        )

    @staticmethod
    def _overlay(req: RequirementResult, parsed: dict | None, state: dict) -> None:
        """用 LLM 解析结果补全缺失字段；已有的显式字段不被覆盖。"""
        if not parsed:
            return
        if req.departure is None and parsed.get("departure"):
            req.departure = str(parsed["departure"])
        if req.travel_date is None and parsed.get("travel_date"):
            try:
                req.travel_date = date.fromisoformat(str(parsed["travel_date"])[:10])
            except ValueError:
                req.travel_date = None
        if req.people_count is None and parsed.get("people_count"):
            req.people_count = int(parsed["people_count"])
        if req.budget is None and parsed.get("budget") is not None:
            try:
                req.budget = Decimal(str(parsed["budget"]))
            except Exception:  # noqa: BLE001
                req.budget = None
        if not req.interests:
            req.interests = _strip_keywords(parsed.get("interests"))
        if parsed.get("physical_level"):
            req.physical_level = int(parsed["physical_level"])
        req.has_elderly = req.has_elderly or bool(parsed.get("has_elderly"))
        req.has_children = req.has_children or bool(parsed.get("has_children"))
        if parsed.get("latest_return_time"):
            req.latest_return_time = str(parsed["latest_return_time"])
        if parsed.get("start_time"):
            req.start_time = str(parsed["start_time"])
        # include/exclude 分开吸收，绝不混用
        req.include_districts = _strip_keywords(parsed.get("include_districts")) or req.include_districts
        req.exclude_districts = _strip_keywords(parsed.get("exclude_districts")) or req.exclude_districts
        req.exclude_attractions = _strip_keywords(parsed.get("exclude_attractions")) or req.exclude_attractions
        if parsed.get("max_one_way_drive_km") is not None:
            req.max_one_way_drive_km = float(parsed["max_one_way_drive_km"])

    @staticmethod
    def _apply_long_term(req: RequirementResult, preferences: list) -> None:
        """已确认的长期偏好只作为默认值，不覆盖用户本次的明确要求。"""
        if not preferences:
            return

        def field(item: Any, name: str) -> str:
            return str(item.get(name) or "") if isinstance(item, dict) else str(getattr(item, name, "") or "")

        interests: list[str] = []
        for item in preferences:
            if field(item, "type") == "interest" and field(item, "content"):
                interests.append(field(item, "content"))
        if interests and not req.interests:
            req.interests = list(dict.fromkeys(interests))

        if not req.has_elderly:
            req.has_elderly = any(
                field(item, "type") == "companion" and "老人" in field(item, "content")
                for item in preferences
            )
        if not req.has_children:
            req.has_children = any(
                field(item, "type") == "companion" and "儿童" in field(item, "content")
                for item in preferences
            )

        low_intensity = any(
            field(item, "type") == "physical" and "低强度" in field(item, "content")
            for item in preferences
        )
        if low_intensity and req.physical_level >= 3:
            req.physical_level = 2

        if not req.departure:
            for item in preferences:
                if field(item, "type") == "departure" and field(item, "content"):
                    text = field(item, "content").replace("常从", "").replace("出发", "")
                    if text:
                        req.departure = text
                        break

    @staticmethod
    def _dedupe_exclusive(req: RequirementResult) -> None:
        cleaned: list[str] = []
        for inc in req.include_districts:
            if not any(inc in ex or ex in inc for ex in req.exclude_districts):
                if inc not in cleaned:
                    cleaned.append(inc)
        req.include_districts = cleaned
        seen: list[str] = []
        for ex in req.exclude_districts:
            if ex not in seen:
                seen.append(ex)
        req.exclude_districts = seen
