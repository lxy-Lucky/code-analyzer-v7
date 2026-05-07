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


def _cache_key(qualified_name: str, lang: str) -> str:
    """语言感知的缓存 key，格式 '{lang}::{qualified_name}'。"""
    return f"{lang}::{qualified_name}"


async def get_cached(
    repo_id: int,
    qualified_name: str,
    db: aiosqlite.Connection,
    lang: str = "zh",
) -> dict | None:
    """
    查询缓存。
    返回 {"llm_summary": str, "llm_tags": list[str]} 或 None（未命中/生成中/出错）。
    同时将 hit_count + 1。
    """
    key = _cache_key(qualified_name, lang)
    async with db.execute(
        "SELECT llm_summary, llm_tags, status FROM llm_cache "
        "WHERE repo_id=? AND qualified_name=?",
        (repo_id, key),
    ) as cur:
        row = await cur.fetchone()

    if row is None or row[2] != "done":
        return None

    # hit_count + 1（fire-and-forget，不等）
    try:
        await db.execute(
            "UPDATE llm_cache SET hit_count = hit_count + 1 WHERE repo_id=? AND qualified_name=?",
            (repo_id, key),
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
    lang: str = "zh",
) -> None:
    """
    触发后台异步生成。本次搜索不等待结果。
    已在 in_flight 或已有 done 缓存的不重复触发。
    """
    key = (repo_id, qualified_name, lang)
    if key in _in_flight:
        return
    _in_flight.add(key)
    asyncio.create_task(_generate_and_store(repo_id, qualified_name, signature, body_text, lang))


async def _generate_and_store(
    repo_id: int,
    qualified_name: str,
    signature: str,
    body_text: str,
    lang: str = "zh",
) -> None:
    """后台生成 LLM summary + tags，写入 llm_cache 表。"""
    flight_key = (repo_id, qualified_name, lang)
    cache_qn   = _cache_key(qualified_name, lang)
    try:
        async with _gen_sem:
            async with get_db() as db:
                async with db.execute(
                    "SELECT status FROM llm_cache WHERE repo_id=? AND qualified_name=?",
                    (repo_id, cache_qn),
                ) as cur:
                    row = await cur.fetchone()
                if row and row[0] == "done":
                    return

                await db.execute(
                    """INSERT INTO llm_cache(repo_id, qualified_name, status, updated_at)
                       VALUES(?,?,'pending',CURRENT_TIMESTAMP)
                       ON CONFLICT(repo_id, qualified_name)
                       DO UPDATE SET status='pending', updated_at=CURRENT_TIMESTAMP""",
                    (repo_id, cache_qn),
                )
                await db.commit()

            result = await _call_llm(signature, body_text, lang)

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
                        cache_qn,
                    ),
                )
                await db.commit()
            logger.debug("llm_cache generated: %s [%s]", qualified_name, lang)

    except Exception as e:
        logger.warning("llm_cache gen failed for %s: %s", qualified_name, e)
        try:
            async with get_db() as db:
                await db.execute(
                    """UPDATE llm_cache SET status='error', error=?, updated_at=CURRENT_TIMESTAMP
                       WHERE repo_id=? AND qualified_name=?""",
                    (str(e), repo_id, cache_qn),
                )
                await db.commit()
        except Exception:
            pass
    finally:
        _in_flight.discard(flight_key)


_LANG_INSTRUCTIONS: dict[str, dict[str, str]] = {
    "zh": {
        "summary_rule": "summary: 一句简洁的中文，不超过30字",
        "tags_rule":    "tags: 2-5个中文关键词（如：用户验证、分页查询、权限校验）",
        "summary_desc": "one concise sentence in Chinese (max 30 chars)",
        "tags_desc":    "2-5 short Chinese keywords",
    },
    "en": {
        "summary_rule": "summary: one concise English sentence, max 20 words",
        "tags_rule":    "tags: 2-5 short English keywords (e.g. pagination, user-auth, file-upload)",
        "summary_desc": "one concise sentence in English (max 20 words)",
        "tags_desc":    "2-5 short English keywords",
    },
    "ja": {
        "summary_rule": "summary: 簡潔な日本語1文、30文字以内",
        "tags_rule":    "tags: 2〜5個の日本語キーワード（例：ユーザー認証、ページング、権限チェック）",
        "summary_desc": "one concise sentence in Japanese (max 30 chars)",
        "tags_desc":    "2-5 short Japanese keywords",
    },
}


async def _call_llm(signature: str, body_text: str, lang: str = "zh") -> dict:
    """
    调用 Ollama 生成 summary + tags。
    严格要求只输出 JSON，不乱发挥。
    """
    import httpx
    from config import OLLAMA_BASE_URL

    inst = _LANG_INSTRUCTIONS.get(lang, _LANG_INSTRUCTIONS["zh"])
    snippet = (body_text or "")[:400]
    prompt = (
        "Analyze the following Java/JavaScript method and respond ONLY with a JSON object.\n"
        f"JSON format: {{\"summary\": \"<{inst['summary_desc']}>\", "
        f"\"tags\": [\"<tag1>\", \"<tag2>\", ...]}}\n"
        "Rules:\n"
        f"- {inst['summary_rule']}\n"
        f"- {inst['tags_rule']}\n"
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
