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
