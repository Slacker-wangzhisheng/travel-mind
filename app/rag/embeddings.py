"""Embedding 抽象与实现。

未配置 Embedding API 时使用 MockEmbedding（确定性，测试不依赖网络）。
"""
from __future__ import annotations

import hashlib
import logging
import math
from abc import ABC, abstractmethod

import httpx

from app.utils.redact import redact

logger = logging.getLogger(__name__)


class EmbeddingProvider(ABC):
    """向量化抽象。"""

    dim: int = 384

    @abstractmethod
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


class MockEmbedding(EmbeddingProvider):
    """确定性 Mock Embedding：基于 token 哈希，测试/离线可用且结果稳定。"""

    def __init__(self, dim: int = 384) -> None:
        self.dim = dim

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [_hash_vector(text, self.dim) for text in texts]


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """OpenAI-compatible Embedding 实现（通过 HTTP）。"""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        dim: int = 1536,
        client: httpx.Client | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.dim = dim
        self._client = client or httpx.Client(timeout=30.0)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        resp = self._client.post(
            f"{self.base_url}/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "input": texts},
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        data.sort(key=lambda d: d["index"])
        return [d["embedding"] for d in data]

    def close(self) -> None:
        self._client.close()


class LocalEmbeddingProvider(EmbeddingProvider):
    """本地语义 Embedding（fastembed + ONNX），无需外部 API。

    默认 BAAI/bge-small-zh-v1.5（512 维），中文语义检索效果可用；
    首次运行会从 HuggingFace 下载模型，国内可设置 HF_ENDPOINT=https://hf-mirror.com。
    """

    def __init__(self, model_name: str = "BAAI/bge-small-zh-v1.5") -> None:
        self.model_name = model_name
        self._model = None
        self.dim = 512 if "bge-small-zh" in model_name else 0

    def _ensure_model(self):
        if self._model is None:
            try:
                from fastembed import TextEmbedding
            except ImportError as exc:  # pragma: no cover - 依赖未安装时给出明确提示
                raise RuntimeError(
                    "未安装 fastembed，无法使用本地 Embedding；请执行 uv add fastembed"
                ) from exc
            self._model = TextEmbedding(model_name=self.model_name)
            if not self.dim:
                vec = next(iter(self._model.embed(["维度探测"])))
                self.dim = len(vec)
        return self._model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        model = self._ensure_model()
        return [[float(x) for x in vec] for vec in model.embed(texts)]


class FallbackEmbeddingProvider(EmbeddingProvider):
    """主 Embedding 失败时回退到备选实现（如 Mock）。

    某些 OpenAI-compatible 服务（例如 DeepSeek）不提供 /embeddings 端点，
    直接抛出 HTTP 404 会让整个行程请求 500；这里兜底为确定性向量，
    牺牲检索精度但保证流程可跑通。

    一旦发生回退，`degraded=True`，调用方必须据此标记 RAG 降级，
    不得把 MockEmbedding 结果当作真实语义检索指标。
    """

    def __init__(self, primary: EmbeddingProvider, fallback: EmbeddingProvider) -> None:
        self.primary = primary
        self.fallback = fallback
        self.dim = getattr(primary, "dim", getattr(fallback, "dim", 384))
        self.degraded = False

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        try:
            vectors = self.primary.embed_documents(texts)
            self.degraded = False
            return vectors
        except Exception as exc:  # noqa: BLE001 - 任何主服务失败都应降级
            logger.warning("主 Embedding 服务调用失败，回退到备选实现：%s", redact(exc))
            self.degraded = True
            return self.fallback.embed_documents(texts)


def build_embedding_provider(settings) -> FallbackEmbeddingProvider:
    """按 EMBEDDING_BACKEND 构造 Embedding（local=fastembed，openai=远程 API）。

    主实现失败时统一回退 Mock，并保留 degraded 标记，避免把 Mock 向量
    当作真实语义检索结果对外宣称。
    """
    if settings.embedding_backend == "local":
        primary: EmbeddingProvider = LocalEmbeddingProvider(settings.local_embedding_model)
        if primary.dim and settings.vector_dim != primary.dim:
            logger.warning(
                "本地 Embedding 维度 %d 与 VECTOR_DIM=%d 不一致；"
                "使用 PostgreSQL 时需设 VECTOR_DIM=%d 并重建 document_chunks 后重新入库",
                primary.dim,
                settings.vector_dim,
                primary.dim,
            )
    else:
        primary = OpenAIEmbeddingProvider(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            model=settings.embedding_model,
            dim=settings.vector_dim,
        )
    fallback_dim = getattr(primary, "dim", 0) or settings.vector_dim
    return FallbackEmbeddingProvider(primary, MockEmbedding(dim=fallback_dim))


def _hash_vector(text: str, dim: int) -> list[float]:
    vec = [0.0] * dim
    tokens = text.lower().split()
    if not tokens:
        tokens = list(text.lower())
    for token in tokens:
        h = hashlib.md5(token.encode()).digest()
        bucket = h[0] % dim
        sign = 1.0 if h[1] % 2 == 0 else -1.0
        vec[bucket] += sign
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]
