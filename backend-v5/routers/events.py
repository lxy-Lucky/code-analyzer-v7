from __future__ import annotations
import asyncio
import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from core.events import register_queue, unregister_queue

router = APIRouter()


@router.get("/events")
async def sse_events():
    q: asyncio.Queue = asyncio.Queue()
    register_queue(q)

    async def generator():
        try:
            while True:
                event = await q.get()
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            unregister_queue(q)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
