from __future__ import annotations
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from models.analysis import AnalysisRequest
from services import analysis_service, repo_service

router = APIRouter()


@router.post("/repos/{repo_id}/analysis")
async def analyze(repo_id: int, body: AnalysisRequest):
    repo = await repo_service.get_repo(repo_id)
    return StreamingResponse(
        analysis_service.analysis_stream(
            repo_id=repo_id,
            repo=repo,
            mode=body.mode,
            base=body.base,
            compare=body.compare,
            qualified_names=body.qualified_names,
            lang=body.lang,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
