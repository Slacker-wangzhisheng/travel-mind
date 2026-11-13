"""将 data/attractions_sjz.json 中的景点写入 pgvector（RAG 知识库）。

用法：uv run python scripts/ingest_sjz.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.rag.embeddings import (  # noqa: E402
    FallbackEmbeddingProvider,
    MockEmbedding,
    OpenAIEmbeddingProvider,
)
from app.rag.ingest import ingest_attractions_file  # noqa: E402
from app.rag.retriever import PGVectorStore  # noqa: E402

_DATA = ROOT / "data" / "attractions_sjz.json"


def main() -> None:
    settings = get_settings()
    embedding = FallbackEmbeddingProvider(
        primary=OpenAIEmbeddingProvider(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            model=settings.embedding_model,
            dim=settings.vector_dim,
        ),
        fallback=MockEmbedding(dim=settings.vector_dim),
    )
    store = PGVectorStore()
    count = ingest_attractions_file(store, embedding, _DATA)
    print(f"已写入 {count} 个 chunk 到 document_chunks")


if __name__ == "__main__":
    main()
