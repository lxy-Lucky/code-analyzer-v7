from __future__ import annotations
import asyncio
from typing import Any

import httpx

from config import OLLAMA_BASE_URL
from indexer.embedder import get_model_info
from retrieval.vector_store import check_qdrant_available_async


async def check_health() -> dict[str, Any]:
    async def _check_ollama() -> tuple[bool, list[str]]:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                r = await client.get(f"{OLLAMA_BASE_URL}/api/tags")
                if r.status_code == 200:
                    return True, [m["name"] for m in r.json().get("models", [])]
        except Exception:
            pass
        return False, []

    qdrant_task = asyncio.create_task(check_qdrant_available_async(timeout=2.0))
    ollama_task = asyncio.create_task(_check_ollama())

    try:
        qdrant_ok, (ollama_ok, models) = await asyncio.wait_for(
            asyncio.gather(qdrant_task, ollama_task),
            timeout=3.0,
        )
    except (asyncio.TimeoutError, Exception):
        # wait_for 超时会 cancel 内部 tasks，.result() 会抛 CancelledError，需安全读取
        def _safe_result(task: asyncio.Task, default):
            if task.done() and not task.cancelled() and task.exception() is None:
                return task.result()
            return default

        qdrant_ok          = bool(_safe_result(qdrant_task, False))
        ollama_ok, models  = _safe_result(ollama_task, (False, []))

    model_info = get_model_info()
    return {
        "fastapi":          True,
        "ollama":           ollama_ok,
        "qdrant":           qdrant_ok,
        "embed_model":      model_info.get("loaded", False),
        "embed_device":     model_info.get("device", ""),
        "available_models": models,
    }


async def debug_repo(repo_id: int) -> dict[str, Any]:
    from retrieval.vector_store import get_qdrant_client
    from qdrant_client.models import Filter, FieldCondition, MatchValue
    import config
    from core.db import get_db

    result: dict[str, Any] = {"repo_id": repo_id, "collections": {}}
    try:
        client = get_qdrant_client()
        for col in (config.QDRANT_COLLECTION_SIG, config.QDRANT_COLLECTION_CTX):
            try:
                info  = client.get_collection(col)
                count = client.count(
                    collection_name=col,
                    count_filter=Filter(must=[
                        FieldCondition(key="repo_id", match=MatchValue(value=repo_id))
                    ]),
                    exact=True,
                ).count
                result["collections"][col] = {
                    "total": info.vectors_count, "this_repo": count
                }
            except Exception as e:
                result["collections"][col] = {"error": str(e)}
    except Exception as e:
        result["error"] = str(e)

    async with get_db() as db:
        async with db.execute("SELECT id, name, path FROM repos") as cur:
            result["all_repos"] = [dict(r) async for r in cur]
        async with db.execute(
            "SELECT COUNT(*) FROM code_units WHERE repo_id=?", (repo_id,)
        ) as cur:
            row = await cur.fetchone()
            result["sqlite_unit_count"] = row[0] if row else 0

    return result
