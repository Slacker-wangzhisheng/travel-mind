"""文本切分与文档构建。

RAG 仅用于景点介绍、历史文化、游玩特色、适合人群、游览注意事项、景点组合建议；
不用于实时天气/路线/票价/开放状态。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.schemas import Attraction

_CITY = "石家庄"


@dataclass
class Chunk:
    text: str
    metadata: dict = field(default_factory=dict)


def split_text(text: str, chunk_size: int = 300, chunk_overlap: int = 50) -> list[str]:
    """按字符滑窗切分，保持简单确定性。"""
    text = text.strip()
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]
    step = max(1, chunk_size - chunk_overlap)
    chunks: list[str] = []
    for i in range(0, len(text), step):
        piece = text[i : i + chunk_size]
        if piece.strip():
            chunks.append(piece)
        if i + chunk_size >= len(text):
            break
    return chunks


def _ticket_text(attr: Attraction) -> str:
    """门票信息文案：优先人工标注，其次数值参考价，最后诚实声明未采集到。"""
    if attr.ticket_note:
        return attr.ticket_note
    if attr.ticket_price is not None:
        return f"参考票价 {attr.ticket_price} 元（以景区官方为准）"
    return "暂无可靠票价数据"


def attraction_to_text(attr: Attraction) -> str:
    parts = [
        f"# {attr.name}",
        f"所属区县：{attr.district}",
        f"分类：{'、'.join(attr.categories)}",
        f"标签：{'、'.join(attr.tags)}",
        f"简介：{attr.introduction}",
        f"门票：{_ticket_text(attr)}",
        f"建议游览时长：约 {attr.suggested_duration_minutes} 分钟",
        f"体力强度：{attr.physical_level}/5",
        f"室内占比：{attr.indoor_ratio}",
        f"适合季节：{'、'.join(attr.suitable_seasons)}",
        f"适合老人：{'是' if attr.suitable_for_elderly else '否'}",
        f"适合儿童：{'是' if attr.suitable_for_children else '否'}",
    ]
    return "\n".join(parts)


def build_attraction_chunks(
    attractions: list[Attraction],
    chunk_size: int = 300,
    chunk_overlap: int = 50,
) -> list[Chunk]:
    """将景点种子数据拆分为带元数据的 RAG 文档。"""
    chunks: list[Chunk] = []
    for attr in attractions:
        text = attraction_to_text(attr)
        meta = {
            "city": _CITY,
            "district": attr.district,
            "attraction": attr.name,
            "category": attr.categories[0] if attr.categories else None,
            "source_url": attr.source_url,
            "updated_at": attr.updated_at,
            "ticket_price": attr.ticket_price,
            "ticket_note": attr.ticket_note,
        }
        for piece in split_text(text, chunk_size, chunk_overlap):
            chunks.append(Chunk(text=piece, metadata=dict(meta)))
    return chunks
