from __future__ import annotations
import asyncio
import hashlib
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger("scanner")
from pathlib import Path
from typing import Any, AsyncIterator

import aiosqlite

from config import SUPPORTED_EXTENSIONS, SCAN_WORKERS
from parsers import parse_file

_SKIP_DIRS: set[str] = {
    ".git", "node_modules", "target", "build", ".idea", "__pycache__",
    "dist", "vendor", "vendors", "lib", "libs", "third_party", "thirdparty",
    "third-party", "bower_components", "webjars", ".mvn", ".gradle",
}

_SKIP_PATH_FRAGMENTS: list[str] = [
    "static/plugins", "static/lib", "static/libs",
    "static/js/plugins", "webapp/static", "WebContent/static",
    "resources/static/js", "resources/static/lib",
    "public/vendor", "assets/vendor", "assets/lib",
    "webjars", "META-INF/resources",
]

_SKIP_FILENAME_KEYWORDS: list[str] = [
    "jquery", "bootstrap", "angular", "react", "vue", "lodash", "underscore",
    "backbone", "ember", "knockout", "zepto", "prototype", "mootools",
    "axios", "moment", "dayjs", "echarts", "highcharts", "d3", "chart.js",
    "three.js", "raphael", "snap", "fabric", "paper", "p5",
    "popper", "tippy", "toastr", "sweetalert", "layer", "layui",
    "datatables", "select2", "chosen", "fullcalendar", "flatpickr",
    "quill", "tinymce", "ckeditor", "codemirror", "ace",
    "font-awesome", "fontawesome", "iconfont",
    "socket.io", "sockjs", "stomp", "mqtt",
    "crypto-js", "jsencrypt", "forge",
    "require.js", "requirejs", "seajs", "systemjs",
    "webpack", "rollup", "vite", "gulp", "grunt",
    "normalize", "reset", "animate",
    ".min.", "-min.", ".bundle.", ".pack.",
    "generated-sources", "generated_sources",
    "spring-beans", "spring-context", "mybatis-config",
    "web-fragment", "persistence",
]

_MAX_JS_SIZE   = 500 * 1024
_MAX_JAVA_SIZE = 5 * 1024 * 1024   # 从 2 MB 调整到 5 MB，避免误杀大型合法源文件


def _is_third_party(file_path: str, language: str) -> bool:
    p = Path(file_path)
    name_lower = p.name.lower()
    path_lower = file_path.lower().replace("\\", "/")
    for kw in _SKIP_FILENAME_KEYWORDS:
        if kw in name_lower:
            logger.debug("skip third-party keyword %r: %s", kw, file_path)
            return True
    for frag in _SKIP_PATH_FRAGMENTS:
        if frag in path_lower:
            logger.debug("skip third-party path fragment %r: %s", frag, file_path)
            return True
    if language in ("javascript", "jsp"):
        try:
            size = p.stat().st_size
            if size > _MAX_JS_SIZE:
                logger.warning("skip oversized JS/JSP file (%.1f KB > %.1f KB): %s",
                               size / 1024, _MAX_JS_SIZE / 1024, file_path)
                return True
        except OSError:
            pass
    if language == "java":
        try:
            size = p.stat().st_size
            if size > _MAX_JAVA_SIZE:
                logger.warning("skip oversized Java file (%.1f KB > %.1f KB): %s",
                               size / 1024, _MAX_JAVA_SIZE / 1024, file_path)
                return True
        except OSError:
            pass
    return False


def _collect_files_sync(repo_path: str) -> list[tuple[str, str]]:
    """同步版本，供 run_in_executor 调用。"""
    result = []
    root = Path(repo_path)
    for p in root.rglob("*"):
        if any(part.lower() in _SKIP_DIRS for part in p.parts):
            continue
        if not p.is_file():
            continue
        suffix = p.suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            continue
        lang = SUPPORTED_EXTENSIONS[suffix]
        abs_path = str(p)
        if _is_third_party(abs_path, lang):
            continue
        result.append((abs_path, lang))
    return result


def _compute_hash_sync(file_path: str) -> str:
    """同步版本，供 run_in_executor 调用。"""
    h = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_one(args: tuple[str, str, str]) -> tuple[str, str, str, list[dict]]:
    """file_path, language, pre_hash → (file_path, language, file_hash, units)
    pre_hash 由调用方传入，避免在 executor 里重复计算 MD5。
    """
    file_path, language, pre_hash = args
    units = parse_file(file_path, language)
    return file_path, language, pre_hash, units


async def scan_repo(
    repo_id: int,
    repo_path: str,
    db: aiosqlite.Connection,
    progress_cb=None,
) -> AsyncIterator[dict[str, Any]]:
    loop = asyncio.get_running_loop()

    # rglob 放 executor，不阻塞事件循环
    files = await loop.run_in_executor(None, _collect_files_sync, repo_path)
    total = len(files)
    updated = skipped = 0

    async with db.execute(
        "SELECT file_path, hash FROM file_hashes WHERE repo_id=?", (repo_id,)
    ) as cur:
        existing = {row[0]: row[1] async for row in cur}

    to_process = []
    for file_path, language in files:
        # 统一使用正斜杠，与 git diff 路径格式保持一致
        rel_path = str(Path(file_path).relative_to(repo_path)).replace("\\", "/")
        # MD5 放 executor，不阻塞事件循环；只算一次，后续 _parse_one 直接复用
        current_hash = await loop.run_in_executor(None, _compute_hash_sync, file_path)
        if existing.get(rel_path) == current_hash:
            skipped += 1
            evt = {"type": "progress", "file": rel_path,
                   "status": "skipped", "total": total, "done": skipped + updated}
            if progress_cb:
                await progress_cb(evt)
            yield evt
        else:
            to_process.append((file_path, language, rel_path, current_hash))

    with ThreadPoolExecutor(max_workers=SCAN_WORKERS) as executor:
        tasks = [
            loop.run_in_executor(executor, _parse_one, (fp, lang, fhash))
            for fp, lang, _, fhash in to_process
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    for (file_path, language, rel_path, _), result in zip(to_process, results):
        if isinstance(result, Exception):
            continue
        _, lang, file_hash, units = result

        await db.execute(
            """INSERT INTO file_hashes(repo_id, file_path, hash, updated_at)
               VALUES(?,?,?,CURRENT_TIMESTAMP)
               ON CONFLICT(repo_id, file_path)
               DO UPDATE SET hash=excluded.hash, updated_at=excluded.updated_at""",
            (repo_id, rel_path, file_hash),
        )
        async with db.execute(
            "SELECT qualified_name FROM code_units WHERE repo_id=? AND file_path=?",
            (repo_id, rel_path),
        ) as _cur:
            _old_qnames = [r[0] async for r in _cur]
        if _old_qnames:
            _ph = ",".join("?" * len(_old_qnames))
            await db.execute(
                f"DELETE FROM call_edges WHERE repo_id=? AND caller_qualified IN ({_ph})",
                [repo_id] + _old_qnames,
            )
        await db.execute(
            "DELETE FROM code_units WHERE repo_id=? AND file_path=?",
            (repo_id, rel_path),
        )

        for unit in units:
            await db.execute(
                """INSERT OR IGNORE INTO code_units
                   (repo_id, file_path, language, unit_type, qualified_name, name,
                    signature, start_line, end_line, body_text, summary, comment,
                    class_name, param_names, param_types, return_type, updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
                (
                    repo_id, rel_path, unit["language"], unit["unit_type"],
                    unit["qualified_name"], unit["name"], unit.get("signature", ""),
                    unit.get("start_line", 0), unit.get("end_line", 0),
                    unit.get("body_text", ""), unit.get("summary", ""),
                    unit.get("comment", ""),
                    unit.get("class_name", ""),
                    json.dumps(unit.get("param_names") or [], ensure_ascii=False),
                    json.dumps(unit.get("param_types") or [], ensure_ascii=False),
                    unit.get("return_type", ""),
                ),
            )
            call_lines = unit.get("call_lines", [])
            if call_lines:
                for callee_name, call_line in call_lines:
                    await db.execute(
                        """INSERT OR IGNORE INTO call_edges
                           (repo_id, caller_qualified, callee_qualified, call_line)
                           VALUES(?,?,?,?)""",
                        (repo_id, unit["qualified_name"], callee_name, call_line),
                    )
            else:
                for callee in unit.get("calls", []):
                    await db.execute(
                        """INSERT OR IGNORE INTO call_edges
                           (repo_id, caller_qualified, callee_qualified, call_line)
                           VALUES(?,?,?,0)""",
                        (repo_id, unit["qualified_name"], callee),
                    )

        await db.commit()
        updated += 1
        evt2 = {
            "type": "progress", "file": rel_path, "status": "updated",
            "units": len(units), "total": total, "done": skipped + updated,
        }
        if progress_cb:
            await progress_cb(evt2)
        yield evt2

    await db.execute(
        "UPDATE repos SET last_scanned_at=CURRENT_TIMESTAMP WHERE id=?", (repo_id,)
    )
    await db.commit()
    yield {"type": "done", "updated": updated, "skipped": skipped, "total": total}


async def get_changed_files_units(
    repo_id: int,
    file_paths: list[str],
    repo_path: str,
    db: aiosqlite.Connection,
) -> list[dict]:
    loop = asyncio.get_running_loop()

    def _body_hash(unit_or_text, signature: str = "") -> str:
        """
        hash = 方法名+参数签名+body内容去空白。
        消除行号偏移、缩进变化、加空格等无意义改动的误报。
        """
        if isinstance(unit_or_text, dict):
            sig  = unit_or_text.get("signature", "") or ""
            body = unit_or_text.get("body_text", "") or ""
        else:
            sig  = signature
            body = unit_or_text or ""
        normalized = re.sub(r'\s+', ' ', (sig + body).strip())
        return hashlib.md5(normalized.encode()).hexdigest()

    all_changed_units = []
    for file_path in file_paths:
        abs_path = (
            str(Path(repo_path) / file_path)
            if not Path(file_path).is_absolute()
            else file_path
        )
        lang = SUPPORTED_EXTENSIONS.get(Path(abs_path).suffix)
        if not lang:
            continue

        rel_path = str(Path(abs_path).relative_to(repo_path)).replace("\\", "/")

        old_body_hashes: dict[str, str] = {}
        old_qnames_watch: list[str] = []
        async with db.execute(
            "SELECT qualified_name, body_text, signature FROM code_units "
            "WHERE repo_id=? AND file_path=?",
            (repo_id, rel_path),
        ) as cur:
            async for row in cur:
                old_qnames_watch.append(row[0])
                # key = qualified_name|signature，区分同名重载方法
                key = f"{row[0]}|{row[2] or ''}"
                old_body_hashes[key] = _body_hash(row[1] or "", row[2] or "")

        units, new_hash = await asyncio.gather(
            loop.run_in_executor(None, parse_file, abs_path, lang),
            loop.run_in_executor(None, _compute_hash_sync, abs_path),
        )

        await db.execute(
            """INSERT INTO file_hashes(repo_id, file_path, hash, updated_at)
               VALUES(?,?,?,CURRENT_TIMESTAMP)
               ON CONFLICT(repo_id, file_path)
               DO UPDATE SET hash=excluded.hash, updated_at=excluded.updated_at""",
            (repo_id, rel_path, new_hash),
        )
        if old_qnames_watch:
            _wph = ",".join("?" * len(old_qnames_watch))
            await db.execute(
                f"DELETE FROM call_edges WHERE repo_id=? AND caller_qualified IN ({_wph})",
                [repo_id] + old_qnames_watch,
            )
        await db.execute(
            "DELETE FROM code_units WHERE repo_id=? AND file_path=?",
            (repo_id, rel_path),
        )
        for unit in units:
            await db.execute(
                """INSERT OR IGNORE INTO code_units
                   (repo_id, file_path, language, unit_type, qualified_name, name,
                    signature, start_line, end_line, body_text, summary, comment,
                    class_name, param_names, param_types, return_type, updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
                (repo_id, rel_path, unit["language"], unit["unit_type"],
                 unit["qualified_name"], unit["name"], unit.get("signature", ""),
                 unit.get("start_line", 0), unit.get("end_line", 0),
                 unit.get("body_text", ""), unit.get("summary", ""),
                 unit.get("comment", ""),
                 unit.get("class_name", ""),
                 json.dumps(unit.get("param_names") or [], ensure_ascii=False),
                 json.dumps(unit.get("param_types") or [], ensure_ascii=False),
                 unit.get("return_type", "")),
            )
        for unit in units:
            call_lines = unit.get("call_lines", [])
            if call_lines:
                for callee_name, call_line in call_lines:
                    await db.execute(
                        """INSERT OR REPLACE INTO call_edges
                           (repo_id, caller_qualified, callee_qualified, call_line)
                           VALUES(?,?,?,?)""",
                        (repo_id, unit["qualified_name"], callee_name, call_line),
                    )
            else:
                for callee in unit.get("calls", []):
                    await db.execute(
                        """INSERT OR REPLACE INTO call_edges
                           (repo_id, caller_qualified, callee_qualified, call_line)
                           VALUES(?,?,?,0)""",
                        (repo_id, unit["qualified_name"], callee),
                    )
        await db.commit()

        for unit in units:
            qn  = unit["qualified_name"]
            sig = unit.get("signature", "") or ""
            key = f"{qn}|{sig}"
            new_bh = _body_hash(unit)
            old_bh = old_body_hashes.get(key)
            if old_bh is None:
                unit["file_path"] = rel_path
                all_changed_units.append(unit)
            elif old_bh != new_bh:
                body = (unit.get("body_text") or "")[:80].replace("\n", "↵")
                unit["file_path"] = rel_path
                all_changed_units.append(unit)

    return all_changed_units
