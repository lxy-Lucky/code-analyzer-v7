from __future__ import annotations
import asyncio
import gc
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, AsyncIterator

import aiosqlite

from config import HEARTBEAT_INTERVAL, EMBED_BATCH_SIZE_GPU, QDRANT_UPSERT_CONCURRENCY
from core.db import get_db
from core.events import broadcast
from indexer.scanner import scan_repo
from indexer.embedder import embed_units_gpu, should_embed
from retrieval.vector_store import (
    check_qdrant_available, get_qdrant_client, stream_upsert_embeddings,
    count_repo_vectors, delete_vectors_for_files,
)

logger = logging.getLogger("scan_service")

_STREAM_BATCH = max(EMBED_BATCH_SIZE_GPU or 256, 256)
_DB_PAGE      = 5000

_scan_progress: dict[int, dict[str, Any]] = {}


def get_scan_progress(repo_id: int) -> dict | None:
    return _scan_progress.get(repo_id)


def _init_progress(repo_id: int) -> None:
    _scan_progress[repo_id] = {
        "status": "scanning",
        "logs": [],
        "current_phase": 0,
        "phases": {
            1: {"done": 0, "total": 0, "desc": ""},
            2: {"done": 0, "total": 0, "desc": ""},
            3: {"done": 0, "total": 0, "desc": ""},
            4: {"done": 0, "total": 0, "desc": ""},
        },
        "embed_done":  0,
        "embed_total": 0,
        "upsert_done": 0,
        "started_at":  time.time(),
    }


def _update_progress(repo_id: int, evt: dict) -> None:
    p = _scan_progress.get(repo_id)
    if p is None:
        return
    t = evt.get("type")
    if t == "phase":
        p["current_phase"] = evt.get("phase", 0)
    elif t == "phase_done":
        ph = evt.get("phase", 0)
        if ph == 1:
            p["phases"][1]["desc"] = f"更新{evt.get('updated_files',0)}文件 {evt.get('updated_units',0)}个方法"
        elif ph == 3:
            p["phases"][3]["desc"] = f"{evt.get('embedded',0)}向量 {evt.get('elapsed',0)}s"
        elif ph == 4:
            p["phases"][4]["desc"] = f"写入{evt.get('written',0)}条"
    elif t == "filter_done":
        p["embed_total"] = evt.get("to_embed", 0)
        p["phases"][2]["desc"] = f"过滤{evt.get('filtered',0)}个，待embed {evt.get('to_embed',0)}个"
    elif t == "embed_progress":
        p["embed_done"] = evt.get("done", 0)
        p["phases"][3]["done"]  = evt.get("done", 0)
        p["phases"][3]["total"] = evt.get("total", 0)
    elif t == "qdrant_progress":
        p["upsert_done"] = evt.get("done", 0)
        p["phases"][4]["done"]  = evt.get("done", 0)
        p["phases"][4]["total"] = evt.get("total", 0)
    elif t in ("scan_complete", "error"):
        p["status"] = "done" if t == "scan_complete" else "error"
    if t in ("progress", "phase", "phase_done", "filter_done",
             "scan_complete", "warning", "error"):
        p["logs"].append(evt)
        if len(p["logs"]) > 100:
            p["logs"] = p["logs"][-100:]


async def scan_stream(repo_id: int, repo: dict, force: bool = False) -> AsyncIterator[bytes]:
    q: asyncio.Queue = asyncio.Queue()

    async def push(evt: dict) -> None:
        _update_progress(repo_id, evt)
        await q.put(("data", evt))

    async def heartbeat_loop() -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            await q.put(("heartbeat", None))

    async def work() -> None:
        _init_progress(repo_id)
        scan_started_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        t_start = time.time()

        async with get_db() as db:
            try:
                await db.execute(
                    "UPDATE repos SET scan_status='scanning', scan_started_at=? WHERE id=?",
                    (scan_started_at, repo_id),
                )
                await db.commit()
            except Exception:
                pass

            try:
                qdrant_ok = check_qdrant_available()
                if not qdrant_ok:
                    await push({"type": "warning", "message": "Qdrant unavailable. SQLite only."})

                # Phase 1
                await push({"type": "phase", "phase": 1, "label": "文件扫描"})
                updated_file_paths: list[str] = []
                async for evt in scan_repo(repo_id, repo["path"], db):
                    await broadcast({**evt, "repo_id": repo_id})
                    await push(evt)
                    if evt.get("status") == "updated" and evt.get("file"):
                        updated_file_paths.append(evt["file"])

                async with db.execute(
                    "SELECT COUNT(*), COUNT(DISTINCT file_path) FROM code_units "
                    "WHERE repo_id=? AND updated_at >= ?",
                    (repo_id, scan_started_at),
                ) as cur:
                    row = await cur.fetchone()
                    updated_unit_count = row[0] if row else 0
                    updated_file_count = row[1] if row else 0

                await push({"type": "phase_done", "phase": 1,
                            "updated_files": updated_file_count,
                            "updated_units": updated_unit_count})

                if not qdrant_ok:
                    await _finish(db, repo_id, t_start, updated_file_count, False, push)
                    return

                # force=True: 强制全量重建，忽略增量状态
                if force:
                    await push({"type": "warning", "message": "强制全量重建向量索引"})
                    scan_started_at = "1970-01-01 00:00:00"
                    async with db.execute(
                        "SELECT COUNT(*), COUNT(DISTINCT file_path) FROM code_units WHERE repo_id=?",
                        (repo_id,),
                    ) as cur:
                        row = await cur.fetchone()
                        updated_unit_count = row[0] if row else 0
                        updated_file_count = row[1] if row else 0
                    if not updated_unit_count:
                        await _finish(db, repo_id, t_start, 0, False, push)
                        return

                # S1/S2: Qdrant 为空但 SQLite 有数据 → 强制全量 re-embed
                elif not updated_unit_count:
                    qdrant_count = count_repo_vectors(repo_id)
                    async with db.execute(
                        "SELECT COUNT(*) FROM code_units WHERE repo_id=?", (repo_id,)
                    ) as cur:
                        row = await cur.fetchone()
                        sqlite_count = row[0] if row else 0

                    if qdrant_count == 0 and sqlite_count > 0:
                        await push({"type": "warning",
                                    "message": f"Qdrant 向量为空，SQLite 有 {sqlite_count} 个单元，强制全量重建向量索引"})
                        scan_started_at = "1970-01-01 00:00:00"
                        async with db.execute(
                            "SELECT COUNT(*), COUNT(DISTINCT file_path) FROM code_units WHERE repo_id=?",
                            (repo_id,),
                        ) as cur:
                            row = await cur.fetchone()
                            updated_unit_count = row[0] if row else 0
                            updated_file_count = row[1] if row else 0
                    else:
                        await _finish(db, repo_id, t_start, updated_file_count, False, push)
                        return

                # Phase 2 — 分页统计 embeddable
                await push({"type": "phase", "phase": 2, "label": "方法过滤"})
                embed_total = filtered_count = 0
                offset = 0
                while True:
                    async with db.execute(
                        "SELECT language, unit_type, name, body_text FROM code_units "
                        "WHERE repo_id=? AND updated_at >= ? LIMIT ? OFFSET ?",
                        (repo_id, scan_started_at, _DB_PAGE, offset),
                    ) as cur:
                        rows = await cur.fetchall()
                    if not rows:
                        break
                    for r in rows:
                        if should_embed({"language": r[0], "unit_type": r[1],
                                         "name": r[2], "body_text": r[3], "calls": []}):
                            embed_total += 1
                        else:
                            filtered_count += 1
                    offset += _DB_PAGE

                await push({"type": "filter_done", "phase": 2,
                            "total_units": updated_unit_count,
                            "filtered": filtered_count,
                            "to_embed": embed_total})

                if not embed_total:
                    await _finish(db, repo_id, t_start, updated_file_count, False, push)
                    return

                # S3: 增量扫描前删掉已更新文件的旧向量，清理改名/删除的方法
                if updated_file_paths:
                    loop_now = asyncio.get_running_loop()
                    client_pre = get_qdrant_client()
                    await loop_now.run_in_executor(
                        None,
                        lambda fps=updated_file_paths: delete_vectors_for_files(repo_id, fps, client_pre),
                    )

                # Phase 3+4 — 三级生产者-消费者流水线
                # 读取(SQLite) → read_q → GPU embed → upsert_q → Qdrant写入
                # 三个协程并发，GPU 永远有下一批在队列排队，消除翻页空窗
                await push({"type": "phase", "phase": 3, "label": "GPU向量化"})
                t_embed  = time.time()
                client   = get_qdrant_client()
                loop     = asyncio.get_running_loop()
                upsert_executor = ThreadPoolExecutor(
                    max_workers=QDRANT_UPSERT_CONCURRENCY,
                    thread_name_prefix="qdrant_upsert",
                )

                # read_q:   存 list[dict] embed batch，预读最多2页保证GPU不等
                # upsert_q: 存 embed结果，None表示结束
                read_q:   asyncio.Queue = asyncio.Queue(maxsize=2)
                upsert_q: asyncio.Queue = asyncio.Queue(maxsize=4)

                embed_done  = 0
                upsert_done = 0

                # ── 生产者：SQLite 分页读取，切成 _STREAM_BATCH 推入 read_q ──
                async def reader() -> None:
                    import json as _json
                    offset = 0
                    while True:
                        async with db.execute(
                            "SELECT qualified_name, name, language, unit_type, file_path, "
                            "start_line, end_line, body_text, signature, summary, "
                            "comment, class_name, param_names, param_types, return_type "
                            "FROM code_units WHERE repo_id=? AND updated_at >= ? "
                            "LIMIT ? OFFSET ?",
                            (repo_id, scan_started_at, _DB_PAGE, offset),
                        ) as cur:
                            rows = await cur.fetchall()
                        if not rows:
                            break
                        page_units = [
                            {
                                "qualified_name": r[0], "name": r[1],
                                "language": r[2],        "unit_type": r[3],
                                "file_path": r[4],       "start_line": r[5],
                                "end_line": r[6],        "body_text": r[7],
                                "signature": r[8],       "summary": r[9],
                                "comment": r[10] or "",
                                "class_name": r[11] or "",
                                "param_names": _json.loads(r[12] or "[]"),
                                "param_types": _json.loads(r[13] or "[]"),
                                "return_type": r[14] or "",
                                "calls": [],             "repo_id": repo_id,
                            }
                            for r in rows
                            if should_embed({"language": r[2], "unit_type": r[3],
                                             "name": r[1], "body_text": r[7], "calls": []})
                        ]
                        for i in range(0, len(page_units), _STREAM_BATCH):
                            await read_q.put(page_units[i: i + _STREAM_BATCH])
                        del page_units
                        offset += _DB_PAGE
                    await read_q.put(None)  # 读取结束信号

                # ── 消费者1：GPU embed，结果推入 upsert_q ──
                async def embedder() -> None:
                    nonlocal embed_done
                    while True:
                        batch = await read_q.get()
                        if batch is None:
                            await upsert_q.put(None)  # 传递结束信号
                            break
                        results = await embed_units_gpu(batch, skip_filter=True)
                        embed_done += len(results)
                        await push({"type": "embed_progress", "phase": 3,
                                    "done": embed_done, "total": embed_total})
                        if results:
                            await upsert_q.put(results)

                # ── 消费者2：Qdrant upsert ──
                async def upserter() -> None:
                    nonlocal upsert_done
                    phase4_started = False
                    while True:
                        results = await upsert_q.get()
                        if results is None:
                            break
                        await loop.run_in_executor(
                            upsert_executor,
                            lambda b=results: stream_upsert_embeddings(repo_id, b, client=client),
                        )
                        upsert_done += len(results)
                        if not phase4_started:
                            await push({"type": "phase", "phase": 4, "label": "写入Qdrant"})
                            phase4_started = True
                        await push({"type": "qdrant_progress", "phase": 4,
                                    "done": upsert_done, "total": embed_total})

                # 三个协程并发启动，等全部完成
                await asyncio.gather(reader(), embedder(), upserter())

                upsert_executor.shutdown(wait=True)

                embed_elapsed = round(time.time() - t_embed, 1)
                await push({"type": "phase_done", "phase": 3,
                            "embedded": embed_done, "elapsed": embed_elapsed})
                await push({"type": "phase_done", "phase": 4, "written": upsert_done})

                elapsed = round(time.time() - t_start, 1)
                await push({"type": "scan_complete", "vector_indexed": True,
                            "elapsed": elapsed, "updated_files": updated_file_count,
                            "total_embedded": embed_done})
                await broadcast({"type": "scan_complete", "repo_id": repo_id})

                try:
                    await db.execute(
                        "UPDATE repos SET scan_status='done', last_scanned_at=CURRENT_TIMESTAMP "
                        "WHERE id=?", (repo_id,),
                    )
                    await db.commit()
                except Exception:
                    pass

            except Exception as e:
                logger.exception("scan_stream error repo_id=%s", repo_id)
                await push({"type": "error", "error": str(e)})
                try:
                    await db.execute(
                        "UPDATE repos SET scan_status='error' WHERE id=?", (repo_id,)
                    )
                    await db.commit()
                except Exception:
                    pass
            finally:
                gc.collect()
                await q.put(("done", None))

    hb_task   = asyncio.create_task(heartbeat_loop())
    work_task = asyncio.create_task(work())

    try:
        while True:
            kind, payload = await q.get()
            if kind == "heartbeat":
                yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"
            elif kind == "data":
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            elif kind == "done":
                break
    finally:
        hb_task.cancel()
        work_task.cancel()
        for t in (hb_task, work_task):
            try:
                await t
            except asyncio.CancelledError:
                pass


async def _finish(
    db: aiosqlite.Connection,
    repo_id: int,
    t_start: float,
    updated_file_count: int,
    vector_indexed: bool,
    push,
) -> None:
    await push({"type": "scan_complete", "vector_indexed": vector_indexed,
                "elapsed": round(time.time() - t_start, 1),
                "updated_files": updated_file_count})
    try:
        await db.execute(
            "UPDATE repos SET scan_status='done', last_scanned_at=CURRENT_TIMESTAMP WHERE id=?",
            (repo_id,),
        )
        await db.commit()
    except Exception:
        pass
    gc.collect()
