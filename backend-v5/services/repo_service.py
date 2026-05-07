from __future__ import annotations
import os
from typing import Any

from core.db import get_db
from core.errors import bad_request, repo_not_found
from retrieval.vector_store import delete_repo_vectors, check_qdrant_available


async def list_repos() -> list[dict]:
    async with get_db() as db:
        async with db.execute("SELECT * FROM repos ORDER BY created_at DESC") as cur:
            return [dict(r) async for r in cur]


async def create_repo(name: str, path: str) -> dict:
    if not os.path.isdir(path):
        raise bad_request(f"Path does not exist: {path}")
    async with get_db() as db:
        try:
            cur = await db.execute(
                "INSERT INTO repos(name, path) VALUES(?,?) RETURNING *",
                (name, path),
            )
            row = await cur.fetchone()
            await db.commit()
            return dict(row)
        except Exception as e:
            raise bad_request(str(e))


async def delete_repo(repo_id: int) -> None:
    # 先清 Qdrant 向量（SQLite 子表通过 CASCADE 自动清理）
    if check_qdrant_available():
        delete_repo_vectors(repo_id)
    async with get_db() as db:
        await db.execute("DELETE FROM repos WHERE id=?", (repo_id,))
        await db.commit()


async def get_repo(repo_id: int) -> dict:
    async with get_db() as db:
        async with db.execute("SELECT * FROM repos WHERE id=?", (repo_id,)) as cur:
            row = await cur.fetchone()
        if not row:
            raise repo_not_found()
        return dict(row)


async def get_stats(repo_id: int) -> dict[str, Any]:
    async with get_db() as db:
        stats: dict[str, Any] = {}
        for lang in ("java", "jsp", "javascript", "xml"):
            async with db.execute(
                "SELECT COUNT(DISTINCT file_path) FROM code_units "
                "WHERE repo_id=? AND language=?",
                (repo_id, lang),
            ) as cur:
                row = await cur.fetchone()
                stats[f"{lang}_files"] = row[0] if row else 0
        async with db.execute(
            "SELECT COUNT(*) FROM code_units WHERE repo_id=?", (repo_id,)
        ) as cur:
            row = await cur.fetchone()
            stats["total_units"] = row[0] if row else 0
        async with db.execute(
            "SELECT last_scanned_at FROM repos WHERE id=?", (repo_id,)
        ) as cur:
            row = await cur.fetchone()
            stats["last_scanned_at"] = row[0] if row else None
        return stats


async def list_reports(repo_id: int) -> list[dict]:
    async with get_db() as db:
        async with db.execute(
            "SELECT id, trigger_type, changed_units, created_at FROM analysis_reports "
            "WHERE repo_id=? ORDER BY created_at DESC LIMIT 20",
            (repo_id,),
        ) as cur:
            return [dict(r) async for r in cur]


async def clear_reports(repo_id: int) -> None:
    async with get_db() as db:
        await db.execute("DELETE FROM analysis_reports WHERE repo_id=?", (repo_id,))
        await db.commit()


async def search_methods(repo_id: int, q: str, limit: int = 20) -> list[dict]:
    if not q.strip():
        return []
    async with get_db() as db:
        async with db.execute(
            "SELECT qualified_name, name, file_path, start_line, language "
            "FROM code_units "
            "WHERE repo_id=? AND (qualified_name LIKE ? OR name LIKE ?) "
            "ORDER BY name LIMIT ?",
            (repo_id, f"%{q}%", f"%{q}%", limit),
        ) as cur:
            return [dict(r) async for r in cur]
