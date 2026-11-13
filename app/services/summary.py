"""会话摘要与上下文裁剪。

- 摘要写入 chat_sessions.summary，永不覆盖 chat_messages 原始记录；
- LLM 摘要失败/关闭时使用确定性摘要兜底；
- 发送给 LLM 的上下文 = 最近 N 条消息 + 摘要 + 当前结构化约束。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_MAX_SUMMARY_INPUT = 4000
_MAX_SUMMARY_LEN = 1200


@dataclass
class ConversationContext:
    summary: str
    recent: list[dict]

    def as_text(self) -> str:
        lines: list[str] = []
        if self.summary:
            lines.append(f"【历史摘要】{self.summary}")
        if self.recent:
            lines.append("【最近对话】")
            for m in self.recent:
                role = "用户" if m.get("role") == "user" else "助手"
                lines.append(f"{role}：{m.get('content', '')}")
        return "\n".join(lines)


def build_context(messages: list, limit: int = 8) -> ConversationContext:
    """最近 limit 条消息（不含摘要本身）。"""
    recent = [
        {"role": m.role, "content": (m.content or "")[:500]}
        for m in messages[-limit:]
    ]
    return ConversationContext(summary="", recent=recent)


def deterministic_summary(messages: list) -> str:
    """把较早的消息压缩成一句可核对的摘要（不臆造内容）。"""
    if not messages:
        return ""
    parts: list[str] = []
    for m in messages:
        text = (m.content or "").strip().replace("\n", " ")
        if not text:
            continue
        who = "用户" if m.role == "user" else "助手"
        parts.append(f"{who}：{text[:80]}")
    joined = "；".join(parts)[:_MAX_SUMMARY_LEN]
    return joined


def llm_summary(llm, messages: list) -> str | None:
    """LLM 摘要；失败返回 None（调用方回退确定性摘要）。"""
    if llm is None or not getattr(llm, "enabled", False):
        return None
    text = "\n".join(
        f"{'用户' if m.role == 'user' else '助手'}：{m.content}" for m in messages
    )[:_MAX_SUMMARY_INPUT]
    if not text.strip():
        return None
    try:
        out = llm.summarize_conversation(text)
        out = (out or "").strip()
        return out[:_MAX_SUMMARY_LEN] if out else None
    except Exception as exc:  # noqa: BLE001 - 摘要失败不应影响原始记录读取
        logger.warning("会话摘要生成失败，使用确定性摘要：%s", exc)
        return None
