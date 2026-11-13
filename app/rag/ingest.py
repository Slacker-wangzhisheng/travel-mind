"""数据导入：将景点种子数据切分、向量化并写入向量存储。"""
from __future__ import annotations

import json
from pathlib import Path

from app.rag.embeddings import EmbeddingProvider
from app.rag.retriever import VectorStore
from app.rag.splitter import build_attraction_chunks
from app.schemas import Attraction
from app.tools.attraction_search import load_attractions


def load_attractions_from(path: Path) -> list[Attraction]:
    """加载任意 JSON 景点数据文件（字段缺失一律 None）。"""
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [Attraction(**item) for item in raw]


def ingest_attractions(store: VectorStore, embedding: EmbeddingProvider) -> int:
    """导入 data/attractions.json，返回写入的 chunk 数量。"""
    attractions = load_attractions()
    chunks = build_attraction_chunks(attractions)
    if not chunks:
        return 0
    embeddings = embedding.embed_documents([c.text for c in chunks])
    store.add(chunks, embeddings)
    return len(chunks)


def ingest_attractions_file(
    store: VectorStore,
    embedding: EmbeddingProvider,
    path: Path,
) -> int:
    """导入指定 JSON 文件中的景点，返回写入的 chunk 数量。"""
    attractions = load_attractions_from(path)
    chunks = build_attraction_chunks(attractions)
    if not chunks:
        return 0
    embeddings = embedding.embed_documents([c.text for c in chunks])
    store.add(chunks, embeddings)
    return len(chunks)
