from __future__ import annotations
import json
from typing import AsyncIterator

import httpx

from config import OLLAMA_BASE_URL, OLLAMA_LLM_MODEL


async def stream_chat(
    messages: list[dict],
    model: str = OLLAMA_LLM_MODEL,
) -> AsyncIterator[str]:
    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream(
            "POST",
            f"{OLLAMA_BASE_URL}/api/chat",
            json={"model": model, "messages": messages, "stream": True},
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                try:
                    data  = json.loads(line)
                    chunk = data.get("message", {}).get("content", "")
                    if chunk:
                        yield chunk
                    if data.get("done"):
                        break
                except json.JSONDecodeError:
                    continue


async def get_available_models() -> list[str]:
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5.0)
            data = resp.json()
            return [m["name"] for m in data.get("models", [])]
    except Exception:
        return []
