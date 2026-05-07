"""
vector_store.py — Qdrant向量存储

2个collection（sig + ctx），文件级增量upsert，RRF融合检索。
"""
from __future__ import annotations
import hashlib
import logging
from typing import Any, Callable

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, VectorParams, PointStruct,
    Filter, FieldCondition, MatchValue,
    FilterSelector,
)

import config

logger = logging.getLogger("vector_store")

_client: QdrantClient | None = None


def get_qdrant_client() -> QdrantClient:
    global _client
    if _client is None:
        _client = QdrantClient(host=config.QDRANT_HOST, port=config.QDRANT_PORT, timeout=30)
        logger.info("Qdrant connected %s:%s", config.QDRANT_HOST, config.QDRANT_PORT)
    return _client


def _ensure_collections(client: QdrantClient):
    existing = {c.name for c in client.get_collections().collections}
    for col in (config.QDRANT_COLLECTION_SIG, config.QDRANT_COLLECTION_CTX):
        if col not in existing:
            client.create_collection(
                collection_name=col,
                vectors_config=VectorParams(size=config.EMBED_DIMENSIONS, distance=Distance.COSINE),
            )
            logger.info("Created collection: %s", col)


def _point_id(repo_id: int, qualified_name: str) -> str:
    return hashlib.md5(f"{repo_id}::{qualified_name}".encode()).hexdigest()


def upsert_file_embeddings(
    repo_id: int,
    file_path: str,
    embed_results: list[dict[str, Any]],
    client: QdrantClient | None = None,
):
    """文件级增量upsert：先删旧向量，再写新向量。"""
    if client is None:
        client = get_qdrant_client()
    _ensure_collections(client)

    for col in (config.QDRANT_COLLECTION_SIG, config.QDRANT_COLLECTION_CTX):
        client.delete(
            collection_name=col,
            points_selector=FilterSelector(
                filter=Filter(must=[
                    FieldCondition(key="repo_id",   match=MatchValue(value=repo_id)),
                    FieldCondition(key="file_path", match=MatchValue(value=file_path)),
                ])
            ),
        )

    if not embed_results:
        return

    sig_points, ctx_points = [], []
    for r in embed_results:
        pid = _point_id(repo_id, r["id"])
        payload = {**r["metadata"], "repo_id": repo_id}
        sig_points.append(PointStruct(id=pid, vector=r["sig_vec"], payload=payload))
        ctx_points.append(PointStruct(id=pid, vector=r["ctx_vec"], payload=payload))

    client.upsert(collection_name=config.QDRANT_COLLECTION_SIG, points=sig_points)
    client.upsert(collection_name=config.QDRANT_COLLECTION_CTX, points=ctx_points)
    logger.debug("Upserted %d vectors for %s repo_id=%d", len(embed_results), file_path, repo_id)


def stream_upsert_embeddings(
    repo_id: int,
    embed_results: list[dict[str, Any]],
    client: QdrantClient | None = None,
):
    """
    流式扫描专用：直接 upsert，不做删除。
    point_id 由 md5(repo_id::qualified_name) 决定，天然去重覆盖。
    内存友好：调用方每批 embed 完就调一次，用完丢弃。
    """
    if not embed_results:
        return
    if client is None:
        client = get_qdrant_client()
    _ensure_collections(client)

    sig_points, ctx_points = [], []
    for r in embed_results:
        pid = _point_id(repo_id, r["id"])
        payload = {**r["metadata"], "repo_id": repo_id}
        sig_points.append(PointStruct(id=pid, vector=r["sig_vec"], payload=payload))
        ctx_points.append(PointStruct(id=pid, vector=r["ctx_vec"], payload=payload))

    client.upsert(collection_name=config.QDRANT_COLLECTION_SIG, points=sig_points)
    client.upsert(collection_name=config.QDRANT_COLLECTION_CTX, points=ctx_points)
    logger.debug("stream_upsert %d vectors repo_id=%d", len(embed_results), repo_id)



def batch_upsert_embeddings(
    repo_id: int,
    embed_results: list[dict[str, Any]],
    batch_size: int = 500,
    progress_cb: Callable[[int, int], None] | None = None,
    client: QdrantClient | None = None,
):
    """全量批量写入（首次扫描用）。调用前应自行清理旧向量。"""
    if client is None:
        client = get_qdrant_client()
    _ensure_collections(client)

    total = len(embed_results)
    for i in range(0, total, batch_size):
        batch = embed_results[i: i + batch_size]
        sig_points, ctx_points = [], []
        for r in batch:
            pid = _point_id(repo_id, r["id"])
            payload = {**r["metadata"], "repo_id": repo_id}
            sig_points.append(PointStruct(id=pid, vector=r["sig_vec"], payload=payload))
            ctx_points.append(PointStruct(id=pid, vector=r["ctx_vec"], payload=payload))
        client.upsert(collection_name=config.QDRANT_COLLECTION_SIG, points=sig_points)
        client.upsert(collection_name=config.QDRANT_COLLECTION_CTX, points=ctx_points)
        done = min(i + batch_size, total)
        logger.info("Qdrant batch %d/%d", done, total)
        if progress_cb:
            progress_cb(done, total)


def delete_repo_vectors(repo_id: int, client: QdrantClient | None = None):
    if client is None:
        client = get_qdrant_client()
    for col in (config.QDRANT_COLLECTION_SIG, config.QDRANT_COLLECTION_CTX):
        try:
            client.delete(
                collection_name=col,
                points_selector=FilterSelector(
                    filter=Filter(must=[FieldCondition(key="repo_id", match=MatchValue(value=repo_id))])
                ),
            )
        except Exception as e:
            logger.warning("delete_repo_vectors col=%s error: %s", col, e)


def rrf_search(
    query_vec: list[float],
    repo_id: int,
    n_results: int = 5,
    language_filter: str | None = None,
    client: QdrantClient | None = None,
) -> list[dict[str, Any]]:
    """RRF融合检索：sig + ctx 两路合并排名后返回 Top-N。"""
    if client is None:
        client = get_qdrant_client()

    must = [FieldCondition(key="repo_id", match=MatchValue(value=repo_id))]
    if language_filter:
        must.append(FieldCondition(key="language", match=MatchValue(value=language_filter)))
    qfilter = Filter(must=must)

    fetch_n = max(n_results * 3, 20)

    def _query(col: str):
        try:
            return client.query_points(
                collection_name=col,
                query=query_vec,
                query_filter=qfilter,
                limit=fetch_n,
                with_payload=True,
            ).points
        except Exception as e:
            logger.warning("Qdrant search col=%s error: %s", col, e)
            return []

    sig_hits = _query(config.QDRANT_COLLECTION_SIG)
    ctx_hits = _query(config.QDRANT_COLLECTION_CTX)

    rrf_scores: dict[str, float] = {}
    all_payload: dict[str, dict] = {}

    for rank, hit in enumerate(sig_hits):
        pid = str(hit.id)
        rrf_scores[pid] = rrf_scores.get(pid, 0) + 1 / (config.RRF_K + rank + 1)
        all_payload[pid] = hit.payload or {}

    for rank, hit in enumerate(ctx_hits):
        pid = str(hit.id)
        rrf_scores[pid] = rrf_scores.get(pid, 0) + 1 / (config.RRF_K + rank + 1)
        all_payload[pid] = hit.payload or {}

    sorted_ids = sorted(rrf_scores, key=lambda x: rrf_scores[x], reverse=True)[:n_results]
    return [
        {**all_payload[pid], "rrf_score": round(rrf_scores[pid], 6)}
        for pid in sorted_ids
    ]


def check_qdrant_available(client: QdrantClient | None = None) -> bool:
    try:
        if client is None:
            client = get_qdrant_client()
        client.get_collections()
        return True
    except Exception:
        return False


async def check_qdrant_available_async(timeout: float = 2.0) -> bool:
    """
    health 接口专用：async + 短 timeout + 独立连接，不阻塞 event loop，
    不影响扫描用的 _client 单例。
    """
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    def _check():
        try:
            # 独立短连接，不复用扫描单例
            c = QdrantClient(
                host=config.QDRANT_HOST,
                port=config.QDRANT_PORT,
                timeout=timeout,
            )
            c.get_collections()
            return True
        except Exception:
            return False

    loop = asyncio.get_event_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(None, _check),
            timeout=timeout + 0.5,   # executor + 一点余量
        )
    except (asyncio.TimeoutError, Exception):
        return False
