from __future__ import annotations
import json
from typing import AsyncIterator

from core.db import get_db
from retrieval.vector_search import search_code
from llm.client import stream_chat
from llm.prompts import get_search_prompt
from services.llm_cache_service import get_cached, trigger_background_gen


async def search_stream(
    repo_id: int,
    query: str,
    n_results: int,
    language: str | None,
    lang: str,
) -> AsyncIterator[bytes]:
    async with get_db() as db:
        try:
            hits = await search_code(query, repo_id, db, n_results, language)

            # LLM 缓存：命中则注入 summary/tags，未命中则触发后台生成
            for hit in hits:
                qn = hit.get("qualified_name", "")
                if not qn:
                    continue
                cached = await get_cached(repo_id, qn, db)
                if cached:
                    hit["llm_summary"] = cached["llm_summary"]
                    hit["llm_tags"] = cached["llm_tags"]
                else:
                    # 异步后台生成，本次搜索不等待
                    trigger_background_gen(
                        repo_id=repo_id,
                        qualified_name=qn,
                        signature=hit.get("signature", ""),
                        body_text=hit.get("body_text", ""),
                    )

            yield f"data: {json.dumps({'type': 'hits', 'hits': hits}, ensure_ascii=False)}\n\n"

            messages = get_search_prompt(query, hits, lang)
            async for chunk in stream_chat(messages):
                yield f"data: {json.dumps({'type': 'chunk', 'text': chunk}, ensure_ascii=False)}\n\n"

            yield f"data: {json.dumps({'type': 'done'})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
