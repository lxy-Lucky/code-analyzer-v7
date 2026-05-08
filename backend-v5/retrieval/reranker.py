from __future__ import annotations
import logging
from typing import Any

import numpy as np

from config import RERANK_TOP_N

logger = logging.getLogger("reranker")


def _build_doc_text(hit: dict[str, Any]) -> str:
    """问题1修复：doc 侧加入 body_text 前300字，提升精排质量。"""
    sig   = hit.get("signature") or hit.get("qualified_name", "")
    summ  = hit.get("summary", "") or ""
    body  = (hit.get("body_text") or "")[:1500]
    return f"{sig}\n{summ}\n{body}".strip()


async def rerank(
    query: str,
    hits: list[dict[str, Any]],
    top_n: int = RERANK_TOP_N,
) -> list[dict[str, Any]]:
    if not hits:
        return hits
    try:
        from indexer.embedder import embed_texts

        doc_texts = [_build_doc_text(h) for h in hits]
        all_texts = [query] + doc_texts
        vecs = await embed_texts(all_texts)
        # embeddings are already L2-normalized, so cosine = dot product
        vecs_np    = np.array(vecs, dtype=np.float32)
        query_vec  = vecs_np[0]
        doc_vecs   = vecs_np[1:]
        scores     = (doc_vecs @ query_vec).tolist()

        scored = [
            {**dict(hit), "rerank_score": round(float(s), 6)}
            for hit, s in zip(hits, scores)
        ]
        scored.sort(key=lambda x: x["rerank_score"], reverse=True)
        logger.info("rerank done: top=%.4f", scored[0]["rerank_score"] if scored else 0)
        return scored[:top_n]

    except Exception as e:
        logger.warning("reranker failed (%s), fallback to vector order", e)
        return hits[:top_n]
