from __future__ import annotations
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from models.search import SearchRequest
from services.search_service import search_stream

router = APIRouter()


@router.post("/repos/{repo_id}/search")
async def search(repo_id: int, body: SearchRequest):
    return StreamingResponse(
        search_stream(repo_id, body.query, body.n_results, body.language, body.lang),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
