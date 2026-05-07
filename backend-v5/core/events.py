from __future__ import annotations
import asyncio
from typing import Any

_queues: list[asyncio.Queue] = []


async def broadcast(event: dict[str, Any]) -> None:
    for q in _queues:
        await q.put(event)


def register_queue(q: asyncio.Queue) -> None:
    _queues.append(q)


def unregister_queue(q: asyncio.Queue) -> None:
    try:
        _queues.remove(q)
    except ValueError:
        pass
