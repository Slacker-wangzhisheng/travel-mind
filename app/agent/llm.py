"""OpenAI-compatible LLM 客户端（可选）。

未配置 LLM_API_KEY 时整体不启用；Agent 改用确定性逻辑，不影响 MVP 运行。
"""
from __future__ import annotations

import json

import httpx

from app.agent import prompts
from app.agent.schemas import ParsedRequest


def _safe_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _resolve_api_key(api_key: str | None) -> str:
    """API Key 只能来自环境变量（LLM_API_KEY）。严禁任何硬编码默认 Key。"""
    if api_key:
        return api_key
    from app.config import get_settings

    return get_settings().llm_api_key


class LLM:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://api.deepseek.com",
        model: str = "deepseek-flash",
        client: httpx.Client | None = None,
    ) -> None:
        self.api_key = _resolve_api_key(api_key)
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._client = client or httpx.Client(timeout=60.0)

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def _chat(self, system: str, user: str) -> str:
        with self._client.stream(
            "POST",
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0,
                "stream": True,
            },
        ) as resp:
            resp.raise_for_status()
            chunks: list[str] = []
            for line in resp.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                payload = line[len("data:") :].strip()
                if payload == "[DONE]":
                    break
                try:
                    obj = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                delta = (obj.get("choices") or [{}])[0].get("delta") or {}
                if delta.get("content"):
                    chunks.append(delta["content"])
        return "".join(chunks)

    def parse_request(self, text: str) -> ParsedRequest | None:
        """解析失败返回 None，由调用方走确定性兜底。"""
        try:
            content = self._chat(prompts.PARSE_SYSTEM, prompts.parse_user(text))
            return ParsedRequest.model_validate(json.loads(content))
        except Exception:  # noqa: BLE001 - 网络/解析错误统一兜底
            return None

    def rank_reason(self, name: str, interests: list[str]) -> str:
        try:
            return self._chat(prompts.RANK_SYSTEM, prompts.rank_user(name, interests)).strip()
        except Exception:  # noqa: BLE001
            return ""

    def parse_refinement(self, text: str, context: str = "") -> dict:
        """解析用户对行程的修改诉求。失败返回空 dict，由调用方走关键词兜底。

        context 为经过裁剪的最近对话与摘要，避免把整段历史塞进 Prompt。
        """
        try:
            content = self._chat(prompts.REFINE_SYSTEM, prompts.refine_user(text, context))
            data = json.loads(content)
            return {
                "exclude_attractions": [str(x) for x in data.get("exclude_attractions") or []],
                "exclude_districts": [str(x) for x in data.get("exclude_districts") or []],
                "max_one_way_drive_km": data.get("max_one_way_drive_km"),
                "wants_charging": bool(data.get("wants_charging")),
                "wants_gas": bool(data.get("wants_gas")),
                "wants_replan": bool(data.get("wants_replan", True)),
            }
        except Exception:  # noqa: BLE001 - 网络/解析错误统一兜底
            return {}

    def summarize_conversation(self, text: str) -> str | None:
        """把较早的对话压缩成摘要；失败返回 None，由确定性摘要兜底。"""
        try:
            return self._chat(prompts.SUMMARY_SYSTEM, text).strip()
        except Exception:  # noqa: BLE001
            return None

    def generate_retrieval_query(
        self, user_text: str, interests: list[str]
    ) -> dict | None:
        """根据用户需求生成向量检索的查询词与元数据过滤条件。失败返回 None。"""
        try:
            content = self._chat(
                prompts.GEN_QUERY_SYSTEM,
                prompts.gen_query_user(user_text, interests),
            )
            data = json.loads(content)
            return {
                "query": str(data.get("query") or "").strip() or None,
                "district": data.get("district"),
                "category": data.get("category"),
            }
        except Exception:  # noqa: BLE001 - 网络/解析错误统一兜底
            return None

    def plan_itinerary(
        self,
        user_section: str,
        knowledge_section: str,
        realtime_section: str,
        feedback: str = "",
    ) -> dict | None:
        """LLM 基于「知识库 + 实时工具」生成结构化行程方案。

        返回 {options: [{title, reason, attractions: [{name, suggested_duration_minutes, note}]}]}。
        失败返回 None，由调用方走确定性兜底。数值字段（时间/预算/路线）不由 LLM 计算。
        """
        try:
            content = self._chat(
                prompts.GEN_SYSTEM,
                prompts.gen_user(user_section, knowledge_section, realtime_section, feedback),
            )
            data = json.loads(content)
            return {
                "options": [
                    {
                        "title": str(opt.get("title") or ""),
                        "reason": str(opt.get("reason") or ""),
                        "attractions": [
                            {
                                "name": str(a.get("name") or "").strip(),
                                "suggested_duration_minutes": _safe_int(a.get("suggested_duration_minutes"), 90),
                                "note": str(a.get("note") or ""),
                            }
                            for a in (opt.get("attractions") or [])
                            if (a.get("name") or "").strip()
                        ],
                    }
                    for opt in (data.get("options") or [])
                    if (opt.get("attractions") or []) and opt.get("title")
                ]
            }
        except Exception:  # noqa: BLE001 - 网络/解析/缺字段错误统一兜底
            return None

    def parse_requirement(self, text: str) -> dict | None:
        """解析用户需求为完整 Requirement 结构化字段（含 include/exclude 区县分离）。

        失败返回 None，由调用方走关键词/现有状态兜底。
        """
        try:
            content = self._chat(prompts.REQUIREMENT_SYSTEM, prompts.parse_user(text))
            data = json.loads(content)
            return {
                "departure": data.get("departure"),
                "travel_date": str(data.get("travel_date") or "").strip() or None,
                "people_count": data.get("people_count"),
                "budget": data.get("budget"),
                "interests": [str(x) for x in (data.get("interests") or [])],
                "physical_level": data.get("physical_level") or 3,
                "has_elderly": bool(data.get("has_elderly")),
                "has_children": bool(data.get("has_children")),
                "latest_return_time": data.get("latest_return_time") or None,
                "start_time": data.get("start_time") or None,
                "include_districts": [str(x) for x in (data.get("include_districts") or [])],
                "exclude_districts": [str(x) for x in (data.get("exclude_districts") or [])],
                "exclude_attractions": [str(x) for x in (data.get("exclude_attractions") or [])],
                "max_one_way_drive_km": data.get("max_one_way_drive_km"),
            }
        except Exception:  # noqa: BLE001 - 网络/解析错误统一兜底
            return None

    def critic(
        self,
        user_section: str,
        proposal_summary: str,
        knowledge_section: str,
        realtime_section: str,
    ) -> dict | None:
        """LLM 对方案质量给出软性建议；决策仍以 Python 硬约束为准。失败返回 None。"""
        try:
            content = self._chat(
                prompts.CRITIC_SYSTEM,
                prompts.critic_user(user_section, proposal_summary, knowledge_section, realtime_section),
            )
            data = json.loads(content)
            return {
                "decision": str(data.get("decision") or "ACCEPT").upper(),
                "soft_issues": [str(x) for x in (data.get("soft_issues") or [])],
                "unsupported_claims": [str(x) for x in (data.get("unsupported_claims") or [])],
                "feedback_for_planner": str(data.get("feedback_for_planner") or ""),
                "feedback_for_retriever": str(data.get("feedback_for_retriever") or ""),
                "route_reason": str(data.get("route_reason") or ""),
            }
        except Exception:  # noqa: BLE001 - 网络/解析错误统一兜底
            return None

    def close(self) -> None:
        self._client.close()
