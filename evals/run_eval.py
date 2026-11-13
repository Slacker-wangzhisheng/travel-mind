"""RAG 检索评测脚本（可重复运行，保留每次配置与结果）。

指标：Recall@5、Recall@10、无结果率、错误区县率、查询耗时（均值 / p95）、排除景点命中率。

用法：
    python evals/run_eval.py --backend local        # 本地真实语义 Embedding（推荐）
    python evals/run_eval.py --backend mock         # 哈希向量，仅链路验证/回归
    python evals/run_eval.py --backend db           # PostgreSQL + 远程 Embedding

重要：
- local 后端使用 fastembed（BAAI/bge-small-zh-v1.5）真实语义向量，指标可用于质量评估；
  首次运行需下载模型，国内可先设置 HF_ENDPOINT=https://hf-mirror.com。
- mock 后端使用 MockEmbedding，向量是哈希而非语义，输出会标记 degraded=true；
  该结果只能用于验证链路与回归对比，不得当作真实语义检索质量指标对外宣称。
- db 后端若主 Embedding 调用失败并回退到备选实现，同样会标记 degraded=true。
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_EVAL_DIR = Path(__file__).resolve().parent
_RESULTS_DIR = _EVAL_DIR / "results"
sys.path.insert(0, str(_ROOT))


def _build_retriever(backend: str, data_path: Path):
    from app.config import get_settings
    from app.rag.embeddings import MockEmbedding
    from app.rag.retriever import MockVectorStore, PGVectorStore, Retriever

    settings = get_settings()
    if backend == "local":
        from app.rag.embeddings import LocalEmbeddingProvider
        from app.rag.ingest import ingest_attractions_file

        embedding = LocalEmbeddingProvider(settings.local_embedding_model)
        store = MockVectorStore()
        chunks = ingest_attractions_file(store, embedding, data_path)
        info = {
            "embedding": f"fastembed:{settings.local_embedding_model}",
            "dim": embedding.dim,
            "chunks": chunks,
            "degraded": False,
            "degraded_reason": None,
        }
        return Retriever(store, embedding), info

    if backend == "mock":
        embedding = MockEmbedding(dim=settings.vector_dim)
        store = MockVectorStore()
        from app.rag.ingest import ingest_attractions_file

        chunks = ingest_attractions_file(store, embedding, data_path)
        info = {
            "embedding": "MockEmbedding",
            "dim": settings.vector_dim,
            "chunks": chunks,
            "degraded": True,
            "degraded_reason": "MockEmbedding 非语义向量，仅用于链路验证与回归对比",
        }
        return Retriever(store, embedding), info

    from app.rag.embeddings import build_embedding_provider

    embedding = build_embedding_provider(settings)
    # 探针：真实环境先验证 Embedding 是否可用
    try:
        embedding.embed_query("评测探针")
    except Exception:  # noqa: BLE001 - 降级标记由 provider 自己维护
        pass
    info = {
        "embedding": (
            f"fastembed:{settings.local_embedding_model}"
            if settings.embedding_backend == "local"
            else settings.embedding_model
        ),
        "dim": embedding.dim,
        "degraded": bool(embedding.degraded),
        "degraded_reason": "主 Embedding 失败已回退到备选实现" if embedding.degraded else None,
    }
    return Retriever(PGVectorStore(), embedding), info


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))
    return ordered[idx]


def evaluate(retriever, dataset: dict, ks: tuple[int, ...] = (5, 10), match: str = "exact") -> dict:
    cases_out: list[dict] = []
    latencies: list[float] = []
    recalls = {k: [] for k in ks}
    no_result = 0
    wrong_district_cases = 0
    district_cases = 0
    excluded_hits = 0
    excluded_total = 0

    for case in dataset.get("cases", []):
        top_k = max(ks)
        started = time.perf_counter()
        try:
            results = retriever.retrieve(
                case["query"], filters=case.get("constraints") or None, top_k=top_k
            )
        except Exception as exc:  # noqa: BLE001 - 检索失败按无结果记录
            results = []
            error = str(exc)
        else:
            error = None
        latency = (time.perf_counter() - started) * 1000
        latencies.append(latency)

        names = [str(r.metadata.get("attraction") or "") for r in results]
        relevant = set(case.get("relevant_attractions") or [])
        excluded = set(case.get("excluded_attractions") or [])

        case_recall = {}
        for k in ks:
            if match == "contains":
                # 高德数据里大量「主景点-子景点」条目，命中子景点也算主景点被召回
                found = sum(1 for rel in relevant if any(rel in n for n in names[:k]))
            else:
                found = len(set(names[:k]) & relevant)
            recall = found / len(relevant) if relevant else 0.0
            recalls[k].append(recall)
            case_recall[f"recall@{k}"] = round(recall, 4)

        if not names:
            no_result += 1

        allowed_district = (case.get("constraints") or {}).get("district")
        if allowed_district:
            district_cases += 1
            wrong = [
                n
                for n, r in zip(names, results, strict=False)
                if r.metadata.get("district") and r.metadata.get("district") != allowed_district
            ]
            if wrong:
                wrong_district_cases += 1
        else:
            wrong = []

        hits = [n for n in names if n in excluded]
        excluded_hits += len(hits)
        excluded_total += len(excluded)

        cases_out.append(
            {
                "id": case.get("id"),
                "query": case["query"],
                "filters": case.get("constraints") or {},
                **case_recall,
                "retrieved": names,
                "excluded_hits": hits,
                "wrong_district": wrong,
                "latency_ms": round(latency, 2),
                "error": error,
            }
        )

    total = len(cases_out) or 1
    totals = {
        **{f"recall@{k}": round(statistics.mean(recalls[k]), 4) if recalls[k] else 0.0 for k in ks},
        "no_result_rate": round(no_result / total, 4),
        "wrong_district_rate": round(wrong_district_cases / district_cases, 4)
        if district_cases
        else 0.0,
        "excluded_hit_rate": round(excluded_hits / excluded_total, 4) if excluded_total else 0.0,
        "latency_avg_ms": round(statistics.mean(latencies), 2) if latencies else 0.0,
        "latency_p95_ms": round(_p95(latencies), 2),
        "cases": len(cases_out),
    }
    return {"totals": totals, "cases": cases_out}


def main() -> int:
    parser = argparse.ArgumentParser(description="RAG 检索评测")
    parser.add_argument("--backend", choices=["local", "mock", "db"], default="local")
    parser.add_argument("--data", default=str(_ROOT / "data" / "attractions_sjz.json"))
    parser.add_argument("--dataset", default=str(_EVAL_DIR / "dataset.json"))
    parser.add_argument(
        "--match",
        choices=["exact", "contains"],
        default="exact",
        help="命中判定：exact=景点名完全一致；contains=子景点（主景点-子景点）也算命中",
    )
    parser.add_argument("--no-save", action="store_true", help="只打印，不写入 results/")
    args = parser.parse_args()

    dataset = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    retriever, info = _build_retriever(args.backend, Path(args.data))
    result = evaluate(retriever, dataset, match=args.match)

    from app.agent import prompts

    report = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "backend": args.backend,
        "data": Path(args.data).name,
        "dataset": Path(args.dataset).name,
        "dataset_version": dataset.get("version"),
        "match": args.match,
        "prompt_version": prompts.PROMPT_VERSION,
        "embedding": info,
        "totals": result["totals"],
        "cases": result["cases"],
    }

    print(json.dumps(report["totals"], ensure_ascii=False, indent=2))
    if info.get("degraded"):
        print(f"[警告] RAG 降级：{info.get('degraded_reason')}；该指标不得视为真实语义检索质量。")

    if not args.no_save:
        _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        out = _RESULTS_DIR / f"{stamp}-{args.backend}.json"
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已保存：{out.relative_to(_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
