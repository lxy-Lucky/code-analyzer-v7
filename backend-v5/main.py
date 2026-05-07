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
