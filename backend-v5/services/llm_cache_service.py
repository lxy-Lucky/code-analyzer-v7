"""
llm_cache_service.py — LLM 懒加载缓存

策略：
- 搜索命中后查 llm_cache 表
- 命中（status=done）：把 llm_summary / llm_tags 注入结果，hit_count + 1
- 未命中或 status=pending/error：asyncio.create_task 异步后台生成
  - Semaphore(2) 控并发，不阻塞本次搜索返回
  - 生成完写库，下次搜索才能看到
"""
from __future__ import annotations
import asyncio
import json
import logging

import aiosqlite

from config import OLLAMA_LLM_MODEL
from core.db import get_db

logger = logging.getLogger("llm_cache_service")

_gen_sem = asyncio.Semaphore(2)

# 追踪正在后台生成中的 (repo_id, qualified_name)，避免重复触发
_in_flight: set[tuple[int, str]] = set()


async def get_cached(
    repo_id: int,
    qualified_name: str,
    db: aiosqlite.Connection,
) -> dict | None:
    """
    查询缓存。
    返回 {"llm_summary": str, "llm_tags": list[str]} 或 None（未命中/生成中/出错）。
    同时将 hit_count + 1。
    """
    async with db.execute(
        "SELECT llm_summary, llm_tags, status FROM llm_cache "
        "WHERE repo_id=? AND qualified_name=?",
        (repo_id, qualified_name),
    ) as cur:
        row = await cur.fetchone()

    if row is None or row[2] != "done":
        return None

    # hit_count + 1（fire-and-forget，不等）
    try:
        await db.execute(
            "UPDATE llm_cache SET hit_count = hit_count + 1 WHERE repo_id=? AND qualified_name=?",
            (repo_id, qualified_name),
        )
        await db.commit()
    except Exception:
        pass

    tags: list[str] = []
    if row[1]:
        try:
            tags = json.loads(row[1])
        except Exception:
            pass

    return {"llm_summary": row[0] or "", "llm_tags": tags}


def trigger_background_gen(
    repo_id: int,
    qualified_name: str,
    signature: str,
    body_text: str,
) -> None:
    """
    触发后台异步生成。本次搜索不等待结果。
    已在 in_flight 或已有 done 缓存的不重复触发。
    """
    key = (repo_id, qualified_name)
    if key in _in_flight:
        return
    _in_flight.add(key)
    asyncio.create_task(_generate_and_store(repo_id, qualified_name, signature, body_text))


async def _generate_and_store(
    repo_id: int,
    qualified_name: str,
    signature: str,
    body_text: str,
) -> None:
    """后台生成 LLM summary + tags，写入 llm_cache 表。"""
    key = (repo_id, qualified_name)
    try:
        async with _gen_sem:
            # 先检查是否已有 done 缓存（并发触发时可能被另一个任务先完成）
            async with get_db() as db:
                async with db.execute(
                    "SELECT status FROM llm_cache WHERE repo_id=? AND qualified_name=?",
                    (repo_id, qualified_name),
                ) as cur:
                    row = await cur.fetchone()
                if row and row[0] == "done":
                    return

                # 写 pending 占位
                await db.execute(
                    """INSERT INTO llm_cache(repo_id, qualified_name, status, updated_at)
                       VALUES(?,?,'pending',CURRENT_TIMESTAMP)
                       ON CONFLICT(repo_id, qualified_name)
                       DO UPDATE SET status='pending', updated_at=CURRENT_TIMESTAMP""",
                    (repo_id, qualified_name),
                )
                await db.commit()

            result = await _call_llm(signature, body_text)

            async with get_db() as db:
                await db.execute(
                    """UPDATE llm_cache
                       SET llm_summary=?, llm_tags=?, model=?, status='done',
                           updated_at=CURRENT_TIMESTAMP
                       WHERE repo_id=? AND qualified_name=?""",
                    (
                        result.get("summary", ""),
                        json.dumps(result.get("tags", []), ensure_ascii=False),
                        OLLAMA_LLM_MODEL,
                        repo_id,
                        qualified_name,
                    ),
                )
                await db.commit()
            logger.debug("llm_cache generated: %s", qualified_name)

    except Exception as e:
        logger.warning("llm_cache gen failed for %s: %s", qualified_name, e)
        try:
            async with get_db() as db:
                await db.execute(
                    """UPDATE llm_cache SET status='error', error=?, updated_at=CURRENT_TIMESTAMP
                       WHERE repo_id=? AND qualified_name=?""",
                    (str(e), repo_id, qualified_name),
                )
                await db.commit()
        except Exception:
            pass
    finally:
        _in_flight.discard(key)


async def _call_llm(signature: str, body_text: str) -> dict:
    """
    调用 Ollama 生成 summary + tags。
    严格要求只输出 JSON，不乱发挥。
    """
    import httpx
    from config import OLLAMA_BASE_URL

    snippet = (body_text or "")[:400]
    prompt = (
        "Analyze the following Java/JavaScript method and respond ONLY with a JSON object.\n"
        "JSON format: {\"summary\": \"<one sentence in Chinese describing what this method does>\", "
        "\"tags\": [\"<tag1>\", \"<tag2>\", ...]}\n"
        "Rules:\n"
        "- summary: one concise Chinese sentence, max 30 characters\n"
        "- tags: 2-5 short Chinese keywords (e.g. 税金, 用户验证, 分页查询)\n"
        "- Output ONLY the JSON object, no markdown, no explanation\n\n"
        f"Method signature: {signature}\n"
        f"Method body (truncated):\n{snippet}"
    )
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={"model": OLLAMA_LLM_MODEL, "prompt": prompt, "stream": False},
        )
        r.raise_for_status()
        raw = r.json().get("response", "").strip()

    # 清理可能的 markdown 围栏
    raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
    try:
        data = json.loads(raw)
        return {
            "summary": str(data.get("summary", ""))[:100],
            "tags": [str(t) for t in data.get("tags", [])[:5]],
        }
    except Exception:
        logger.warning("llm_cache: failed to parse JSON response: %r", raw[:200])
        return {"summary": "", "tags": []}
