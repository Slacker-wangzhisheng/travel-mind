"""KnowledgeAgent：依据需求 + Critic 反馈生成检索查询，执行 RAG 与规则检索。

- RAG（Embedding + pgvector / Mock）按 metadata 过滤，返回 top-K 文档（含来源/区县/分类）。
- 空结果/低相关度做查询改写重试；重试后仍不足则标记 sufficient=False → 触发 RESEARCH。
- 规则检索 + 向量检索合并、按景点名去重；被检索到的景点真正进入候选集合。
- 「不去」区县/景点在检索后再次确定性过滤，绝不影响 include 过滤。
- 真实环境不使用 MockEmbedding 伪装语义检索；Embedding 降级时标记 degraded=True。
"""
from __future__ import annotations

import logging
from typing import Any

from app.agents.base import Agent
from app.agents.schemas import KnowledgeDocument, KnowledgeResult
from app.agents.support import build_ranked_candidates
from app.schemas import Attraction
from app.services.relevance import is_relevant
from app.tools.attraction_search import AttractionSearchInput, search_attractions
from app.utils.redact import redact

logger = logging.getLogger(__name__)

_TOP_K = 6
_MIN_CANDIDATES = 1


class KnowledgeAgent(Agent):
    name = "KnowledgeAgent"

    def run(self, state: dict, deps: Any) -> dict:
        req = state.get("requirement_result")
        if req is None:
            return self._insufficient(state, "缺少需求结果")

        interests = req.interests or []
        rule_attrs = self._rule_search(req, interests)

        query, filters = self._build_query(req, state, deps)
        docs = self._retrieve(deps, query, filters, top_k=_TOP_K)

        # 查询改写重试：空结果或相关度过低时重写一次
        retry_reason = ''
        if not docs:
            query2, filters2 = self._build_query(req, state, deps, force_rewrite=True)
            docs = self._retrieve(deps, query2, filters2, top_k=_TOP_K)
            if docs:
                query, filters = query2, filters2
            else:
                retry_reason = "无有效检索结果"

        # 向量检索命中的景点并入候选（真正进入候选集合）
        docs_attrs = self._documents_to_attractions(docs, req)
        merged = self._dedupe(rule_attrs + docs_attrs)
        # 「不去」区县/景点确定性过滤
        merged = self._apply_exclusions(merged, req)
        # 兴趣相关性过滤：零相关景点不能留在候选里被规划器选中
        if interests:
            kept = [a for a in merged if is_relevant(a, interests)]
            if len(kept) != len(merged):
                logger.info(
                    "按兴趣过滤掉 %d 个不相关候选（兴趣：%s）",
                    len(merged) - len(kept),
                    "、".join(interests),
                )
            merged = kept
            if not merged:
                retry_reason = retry_reason or "没有与兴趣匹配的景点"

        knowledge = KnowledgeResult(
            query=query,
            filters=filters,
            documents=docs,
            sources=list(dict.fromkeys(d.source_url for d in docs if d.source_url)),
            candidate_attractions=merged,
            max_score=max((d.score for d in docs), default=0.0),
            sufficient=bool(merged) and len(merged) >= _MIN_CANDIDATES,
            retry_reason=retry_reason,
            degraded=bool(state.get("degraded_services")) or self._embedding_degraded(deps),
            rule_hits=len(rule_attrs),
            vector_hits=len(docs),
        )
        if not knowledge.sufficient and not knowledge.retry_reason:
            knowledge.retry_reason = "候选不足" if not merged else "知识来源不足"

        ranked = build_ranked_candidates(
            merged, interests, bool(req.has_elderly), bool(state.get("weather"))
        )
        knowledge.ranked_candidates = ranked

        degraded = list(state.get("degraded_services") or [])
        if self._embedding_degraded(deps) and "Embedding" not in degraded:
            degraded.append("Embedding")
            logger.warning("RAG 降级：Embedding 主服务失败，已回退到备选实现")

        return {
            "knowledge_result": knowledge,
            "candidate_attractions": merged,
            "ranked_candidates": ranked,
            "retrieved_documents": [
                {"text": d.text, "metadata": self._doc_meta(d)} for d in docs
            ],
            "retrieval_query": query,
            "degraded_services": degraded,
            "current_agent": self.name,
            "next_agent": "RealtimeAgent",
            "route_reason": "候选集就绪",
        }

    @staticmethod
    def _embedding_degraded(deps: Any) -> bool:
        return bool(getattr(getattr(deps.retriever, "embedding", None), "degraded", False))

    # ---------------------------------------------------------------- 查询
    def _build_query(self, req: Any, state: dict, deps: Any, force_rewrite: bool = False) -> tuple[str, dict]:
        feedback = state.get("knowledge_feedback") or ""
        base = " ".join(req.interests) or "石家庄 旅游"
        if force_rewrite and feedback:
            base = (base + " " + feedback).strip()
        filters: dict[str, str] = {}
        if req.include_districts:
            # include 过滤：明确想去的区县生效，与 exclude_districts 完全独立
            filters["district"] = req.include_districts[0]
        llm = getattr(deps, "llm", None)
        if llm and getattr(llm, "enabled", False):
            try:
                rq = llm.generate_retrieval_query(
                    state.get("user_request", ""), req.interests
                )
            except Exception:  # noqa: BLE001
                rq = None
            if rq:
                if rq.get("query"):
                    base = base if force_rewrite else rq["query"]
                if rq.get("district") and not filters:
                    filters["district"] = rq["district"]
                if rq.get("category"):
                    filters["category"] = rq["category"]
        return base, filters

    # ---------------------------------------------------------------- 检索
    def _retrieve(self, deps: Any, query: str, filters: dict, top_k: int) -> list[KnowledgeDocument]:
        try:
            results = deps.retriever.retrieve(query, filters=filters or None, top_k=top_k)
        except Exception as exc:  # noqa: BLE001 - RAG 可选，失败降级为空
            logger.warning("RAG 检索失败，降级为空：%s", redact(exc))
            return []
        return [
            KnowledgeDocument(
                text=d.text,
                score=float(d.score or 0.0),
                doc_id=str(id(d)),
                attraction=str(d.metadata.get("attraction") or ""),
                district=d.metadata.get("district"),
                category=d.metadata.get("category"),
                source_url=d.source_url,
                updated_at=d.updated_at,
            )
            for d in results
        ]

    def _rule_search(self, req: Any, interests: list[str]) -> list[Attraction]:
        season = _season_of(req.travel_date)
        inp = AttractionSearchInput(
            interests=interests,
            district=None,
            max_physical_level=2 if req.has_elderly else None,
            require_elderly_friendly=bool(req.has_elderly),
            require_children_friendly=bool(req.has_children),
            season=season,
            top_k=_TOP_K,
        )
        return search_attractions(inp)

    # ---------------------------------------------------------------- 转换
    def _documents_to_attractions(self, docs: list[KnowledgeDocument], req: Any) -> list[Attraction]:
        by_name = _all_attractions_by_name()
        out: list[Attraction] = []
        for d in docs:
            name = (d.attraction or "").strip()
            if not name:
                continue
            base_attr = by_name.get(name)
            if base_attr is not None:
                out.append(base_attr.model_copy(deep=True))
            else:
                out.append(
                    Attraction(
                        name=name,
                        district=d.district or "",
                        categories=[d.category] if d.category else [],
                        introduction=d.text[:500],
                        physical_level=3,
                        indoor_ratio=0.3,
                        source_url=d.source_url,
                        updated_at=d.updated_at,
                    )
                )
        return out

    @staticmethod
    def _doc_meta(d: KnowledgeDocument) -> dict:
        return {
            "attraction": d.attraction,
            "district": d.district,
            "category": d.category,
            "source_url": d.source_url,
            "updated_at": d.updated_at,
            "doc_id": d.doc_id,
        }

    @staticmethod
    def _dedupe(attrs: list[Attraction]) -> list[Attraction]:
        seen: set[str] = set()
        out: list[Attraction] = []
        for a in attrs:
            key = (a.name.strip().lower(), a.district)
            if key in seen:
                continue
            seen.add(key)
            out.append(a)
        return out

    @staticmethod
    def _apply_exclusions(attrs: list[Attraction], req: Any) -> list[Attraction]:
        ex_names = {n.strip() for n in req.exclude_attractions if n}
        ex_districts = {d.strip() for d in req.exclude_districts if d}
        out: list[Attraction] = []
        for a in attrs:
            if a.name in ex_names:
                continue
            if any(d and (d in a.district or a.district in d) for d in ex_districts):
                continue
            out.append(a)
        return out

    @staticmethod
    def _insufficient(state: dict, reason: str) -> dict:
        return {
            "knowledge_result": KnowledgeResult(retry_reason=reason, sufficient=False),
            "candidate_attractions": [],
            "ranked_candidates": [],
            "current_agent": "KnowledgeAgent",
            "degraded_services": list(state.get("degraded_services") or []),
        }


def _season_of(d: Any) -> str | None:
    if d is None:
        return None
    m = getattr(d, "month", None)
    if m in (3, 4, 5):
        return "春季"
    if m in (6, 7, 8):
        return "夏季"
    if m in (9, 10, 11):
        return "秋季"
    if m is None:
        return None
    return "冬季"


def _all_attractions_by_name() -> dict[str, Attraction]:
    from app.tools.attraction_search import load_attractions

    return {a.name: a for a in load_attractions()}
