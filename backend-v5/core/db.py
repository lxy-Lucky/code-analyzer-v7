from __future__ import annotations
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import aiosqlite

from config import DB_PATH


async def _init_connection(db: aiosqlite.Connection) -> None:
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    db.row_factory = aiosqlite.Row


@asynccontextmanager
async def get_db() -> AsyncIterator[aiosqlite.Connection]:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await _init_connection(db)
        yield db


async def init_db() -> None:
    schema = (Path(__file__).parent.parent / "db" / "schema.sql").read_text()
    async with get_db() as db:
        await db.executescript(schema)
        await db.commit()
        await _migrate(db)


async def _migrate(db: aiosqlite.Connection) -> None:
    """幂等迁移：给已有 DB 补充新列和新索引，CREATE IF NOT EXISTS 对表结构变更无效。"""
    # repos 表补 scan_started_at 列
    async with db.execute("PRAGMA table_info(repos)") as cur:
        cols = {row[1] async for row in cur}
    if "scan_started_at" not in cols:
        await db.execute("ALTER TABLE repos ADD COLUMN scan_started_at TIMESTAMP")
        await db.commit()

    # 补 (repo_id, updated_at) 复合索引
    async with db.execute("PRAGMA index_list(code_units)") as cur:
        indexes = {row[1] async for row in cur}
    if "idx_code_units_repo_updated" not in indexes:
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_code_units_repo_updated "
            "ON code_units(repo_id, updated_at)"
        )
        await db.commit()

    # 补 call_edges 复合索引
    async with db.execute("PRAGMA index_list(call_edges)") as cur:
        indexes = {row[1] async for row in cur}
    if "idx_call_edges_repo_caller" not in indexes:
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_call_edges_repo_caller "
            "ON call_edges(repo_id, caller_qualified)"
        )
        await db.commit()
    if "idx_call_edges_repo_callee" not in indexes:
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_call_edges_repo_callee "
            "ON call_edges(repo_id, callee_qualified)"
        )
        await db.commit()
