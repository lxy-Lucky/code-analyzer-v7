from __future__ import annotations
import asyncio
import json

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from models.repo import RepoCreate
from services import repo_service, scan_service, watch_service, health_service
from core.db import get_db
from core.errors import repo_not_found
from git.hook_manager import install_hook, uninstall_hook, is_hook_installed
from watcher.file_watcher import get_watcher

router = APIRouter()


# ── Repo CRUD ─────────────────────────────────────────────────────────────────

@router.get("/repos")
async def list_repos():
    return await repo_service.list_repos()


@router.post("/repos")
async def create_repo(body: RepoCreate):
    return await repo_service.create_repo(body.name, body.path)


@router.delete("/repos/{repo_id}")
async def delete_repo(repo_id: int):
    await repo_service.delete_repo(repo_id)
    return {"ok": True}


# ── Scan ──────────────────────────────────────────────────────────────────────

@router.post("/repos/{repo_id}/scan")
async def scan(repo_id: int):
    repo = await repo_service.get_repo(repo_id)
    return StreamingResponse(
        scan_service.scan_stream(repo_id, repo),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/repos/{repo_id}/scan/status")
async def scan_status(repo_id: int):
    progress = scan_service.get_scan_progress(repo_id)
    if progress is not None:
        return progress
    async with get_db() as db:
        async with db.execute(
            "SELECT scan_status FROM repos WHERE id=?", (repo_id,)
        ) as cur:
            row = await cur.fetchone()
            db_status = row[0] if row else "idle"
    return {"status": db_status, "logs": [], "phases": {},
            "current_phase": 0, "embed_done": 0, "embed_total": 0, "upsert_done": 0}


# ── Stats ─────────────────────────────────────────────────────────────────────

@router.get("/repos/{repo_id}/stats")
async def repo_stats(repo_id: int):
    return await repo_service.get_stats(repo_id)


# ── Watcher ───────────────────────────────────────────────────────────────────

@router.post("/repos/{repo_id}/watch/start")
async def start_watch(repo_id: int):
    repo = await repo_service.get_repo(repo_id)
    loop = asyncio.get_event_loop()
    get_watcher().start(
        repo["path"], loop,
        watch_service.make_on_change(repo_id, repo["path"]),
    )
    return {"watching": True, "path": repo["path"]}


@router.post("/repos/{repo_id}/watch/stop")
async def stop_watch(repo_id: int):
    get_watcher().stop()
    return {"watching": False}


@router.get("/repos/{repo_id}/watch/status")
async def watch_status(repo_id: int):
    w = get_watcher()
    return {"watching": w.is_running, "path": w.watching_path}


@router.get("/repos/{repo_id}/watch/changes")
async def get_watch_changes(repo_id: int):
    methods = watch_service.get_changes(repo_id)
    return {"methods": list(methods.keys()), "count": len(methods)}


@router.delete("/repos/{repo_id}/watch/changes")
async def clear_watch_changes(repo_id: int):
    watch_service.clear_changes(repo_id)
    return {"ok": True}


# ── Git Hook ──────────────────────────────────────────────────────────────────

@router.post("/repos/{repo_id}/hook/install")
async def hook_install(repo_id: int):
    repo = await repo_service.get_repo(repo_id)
    return install_hook(repo["path"])


@router.post("/repos/{repo_id}/hook/uninstall")
async def hook_uninstall(repo_id: int):
    repo = await repo_service.get_repo(repo_id)
    return uninstall_hook(repo["path"])


@router.get("/repos/{repo_id}/hook/status")
async def hook_status(repo_id: int):
    repo = await repo_service.get_repo(repo_id)
    return {"installed": is_hook_installed(repo["path"])}


@router.post("/git/hook-trigger")
async def hook_trigger(repo_path: str):
    from git.diff_parser import get_diff_vs_head
    from core.events import broadcast
    async with get_db() as db:
        async with db.execute(
            "SELECT * FROM repos WHERE path=?", (repo_path,)
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return {"ok": False, "error": "Repo not registered"}
    repo = dict(row)
    changed_files = get_diff_vs_head(repo_path)
    await broadcast({
        "type": "hook_triggered",
        "repo_id": repo["id"],
        "files": [f["file_path"] for f in changed_files],
    })
    return {"ok": True, "changed_files": len(changed_files)}


# ── Methods autocomplete ──────────────────────────────────────────────────────

@router.get("/repos/{repo_id}/methods")
async def search_methods(
    repo_id: int,
    q: str = Query(""),
    limit: int = Query(20, le=50),
):
    return await repo_service.search_methods(repo_id, q, limit)


# ── Reports ───────────────────────────────────────────────────────────────────

@router.get("/repos/{repo_id}/reports")
async def list_reports(repo_id: int):
    return await repo_service.list_reports(repo_id)


@router.delete("/repos/{repo_id}/reports")
async def clear_reports(repo_id: int):
    await repo_service.clear_reports(repo_id)
    return {"ok": True}


# ── Health & Debug ────────────────────────────────────────────────────────────

@router.get("/health")
async def health():
    return await health_service.check_health()


@router.get("/repos/{repo_id}/debug")
async def debug_repo(repo_id: int):
    return await health_service.debug_repo(repo_id)
