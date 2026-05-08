from __future__ import annotations
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.db import init_db
from routers import repos, search, analysis, events

app = FastAPI(title="CodeAnalyzer API", version="5.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(repos.router,     prefix="/api")
app.include_router(search.router,    prefix="/api")
app.include_router(analysis.router,  prefix="/api")
app.include_router(events.router,    prefix="/api")


@app.on_event("startup")
async def startup() -> None:
    Path("db").mkdir(exist_ok=True)
    await init_db()
    from indexer.embedder import preload
    await preload()


@app.on_event("shutdown")
async def shutdown() -> None:
    # 等待 GPU executor 当前任务完成，避免 embedding 写到一半
    from indexer.embedder import _executor
    if _executor is not None:
        _executor.shutdown(wait=True)

    # 把进程退出时仍处于 scanning 状态的 repo 重置为 idle，避免 DB 状态卡死
    from core.db import get_db
    try:
        async with get_db() as db:
            await db.execute(
                "UPDATE repos SET scan_status='idle' WHERE scan_status='scanning'"
            )
            await db.commit()
    except Exception:
        pass
