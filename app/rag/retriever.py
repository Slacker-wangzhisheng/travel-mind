"""向量存储与检索。

- VectorStore：抽象接口（Mock / PGVector 两种实现）。
- Retriever：组合 Embedding + VectorStore，支持元数据过滤，返回来源与更新时间。
"""
from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from sqlalchemy import select

from app.db.models import DocumentChunk
from app.db.session import get_session
from app.rag.embeddings import EmbeddingProvider
from app.rag.splitter import Chunk


@dataclass
class SearchResult:
    text: str
    score: float
    metadata: dict = field(default_factory=dict)

    @property
    def source_url(self) -> str | None:
        return self.metadata.get("source_url")

    @property
    def updated_at(self) -> str | None:
        return self.metadata.get("updated_at")


class VectorStore(ABC):
    @abstractmethod
    def add(self, chunks: list[Chunk], embeddings: list[list[float]]) -> None: ...

    @abstractmethod
    def similarity_search(
        self,
        query_embedding: list[float],
        filters: dict[str, str],
        top_k: int = 5,
    ) -> list[SearchResult]: ...


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)


class MockVectorStore(VectorStore):
    """内存向量存储，测试/离线可用。"""

    def __init__(self) -> None:
        self._chunks: list[Chunk] = []
        self._embeddings: list[list[float]] = []

    def add(self, chunks: list[Chunk], embeddings: list[list[float]]) -> None:
        self._chunks.extend(chunks)
        self._embeddings.extend(embeddings)

    def similarity_search(
        self,
        query_embedding: list[float],
        filters: dict[str, str],
        top_k: int = 5,
    ) -> list[SearchResult]:
        scored: list[SearchResult] = []
        for chunk, emb in zip(self._chunks, self._embeddings, strict=False):
            if not _match_metadata(chunk.metadata, filters):
                continue
            scored.append(
                SearchResult(text=chunk.text, score=_cosine(query_embedding, emb), metadata=chunk.metadata)
            )
        scored.sort(key=lambda r: r.score, reverse=True)
        return scored[:top_k]


class PGVectorStore(VectorStore):
    """基于 PostgreSQL + pgvector 的存储。"""

    def add(self, chunks: list[Chunk], embeddings: list[list[float]]) -> None:
        with get_session() as session:
            for chunk, emb in zip(chunks, embeddings, strict=False):
                session.add(
                    DocumentChunk(
                        city=chunk.metadata.get("city") or "石家庄",
                        district=chunk.metadata.get("district"),
                        attraction=chunk.metadata.get("attraction") or "",
                        category=chunk.metadata.get("category"),
                        source_url=chunk.metadata.get("source_url"),
                        updated_at=chunk.metadata.get("updated_at"),
                        ticket_price=chunk.metadata.get("ticket_price"),
                        ticket_note=chunk.metadata.get("ticket_note"),
                        chunk_text=chunk.text,
                        embedding=emb,
                    )
                )
            session.commit()

    def similarity_search(
        self,
        query_embedding: list[float],
        filters: dict[str, str],
        top_k: int = 5,
    ) -> list[SearchResult]:
        stmt = select(DocumentChunk)
        if filters.get("city"):
            stmt = stmt.where(DocumentChunk.city == filters["city"])
        if filters.get("district"):
            stmt = stmt.where(DocumentChunk.district == filters["district"])
        if filters.get("attraction"):
            stmt = stmt.where(DocumentChunk.attraction == filters["attraction"])
        if filters.get("category"):
            stmt = stmt.where(DocumentChunk.category == filters["category"])
        stmt = stmt.order_by(DocumentChunk.embedding.cosine_distance(query_embedding)).limit(top_k)
        with get_session() as session:
            rows = session.scalars(stmt).all()

        results: list[SearchResult] = []
        for row in rows:
            dist = _cosine(query_embedding, list(row.embedding))
            results.append(
                SearchResult(
                    text=row.chunk_text,
                    score=dist,
                    metadata={
                        "city": row.city,
                        "district": row.district,
                        "attraction": row.attraction,
                        "category": row.category,
                        "source_url": row.source_url,
                        "updated_at": row.updated_at,
                        "ticket_price": row.ticket_price,
                        "ticket_note": row.ticket_note,
                    },
                )
            )
        return results


def _match_metadata(meta: dict, filters: dict[str, str]) -> bool:
    for key, value in filters.items():
        if value is None:
            continue
        if meta.get(key) != value:
            return False
    return True


class Retriever:
    """组合 Embedding 与 VectorStore，提供检索入口。"""

    def __init__(self, store: VectorStore, embedding: EmbeddingProvider) -> None:
        self.store = store
        self.embedding = embedding

    def retrieve(
        self,
        query: str,
        filters: dict[str, str] | None = None,
        top_k: int = 5,
    ) -> list[SearchResult]:
        qv = self.embedding.embed_query(query)
        return self.store.similarity_search(qv, filters or {}, top_k=top_k)
