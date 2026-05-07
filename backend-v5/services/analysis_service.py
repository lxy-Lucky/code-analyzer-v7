from __future__ import annotations
import json
import logging
from typing import AsyncIterator

from core.db import get_db
from indexer.graph_builder import get_impact_chain
from llm.client import stream_chat
from llm.prompts import get_impact_prompt
from git.diff_parser import get_diff_vs_head, get_diff_between, map_lines_to_units
from services.watch_service import get_changes

logger = logging.getLogger("analysis")


async def analysis_stream(
    repo_id: int,
    repo: dict,
    mode: str,
    base: str,
    compare: str | None,
    qualified_names: list[str] | None,
    lang: str,
) -> AsyncIterator[bytes]:
    async with get_db() as db:
        try:
            source_event = None

            # 确定 qualified_names
            if qualified_names is not None:
                # 直接指定方法列表（methods 模式）
                logger.info("[analysis] repo=%s mode=methods qn_count=%s", repo_id, len(qualified_names))
            elif mode == "recent":
                watched = get_changes(repo_id)
                qualified_names = list(watched.keys())
                logger.info("[analysis] repo=%s mode=recent qn_count=%s", repo_id, len(qualified_names))
                source_event = {
                    "type": "changed_methods",
                    "methods": qualified_names,
                    "source": "watch",
                }
            else:
                # 从 git diff 推算
                repo_path = repo["path"]
                logger.info("[analysis] repo=%s mode=%s base=%r compare=%r path=%r",
                            repo_id, mode, base, compare, repo_path)

                if mode == "between" and compare:
                    changed_files = get_diff_between(repo_path, base, compare)
                else:
                    changed_files = get_diff_vs_head(repo_path)

                logger.info("[analysis] git diff found %s file(s): %s",
                            len(changed_files),
                            [f["file_path"] for f in changed_files])

                yield f"data: {json.dumps({'type': 'diff', 'files': [f['file_path'] for f in changed_files]}, ensure_ascii=False)}\n\n"

                # git 始终返回正斜杠路径，统一规范化防止 Windows 下与 DB 路径不匹配
                file_paths = [f["file_path"].replace("\\", "/") for f in changed_files]
                if file_paths:
                    placeholders = ",".join("?" * len(file_paths))
                    async with db.execute(
                        # REPLACE 兼容 DB 中已有的反斜杠旧数据，无需重新扫描
                        f"SELECT qualified_name, file_path, start_line, end_line "
                        f"FROM code_units WHERE repo_id=? "
                        f"AND REPLACE(file_path, '\\', '/') IN ({placeholders})",
                        [repo_id] + file_paths,
                    ) as cur:
                        units_by_file: dict[str, list[dict]] = {}
                        async for row in cur:
                            # key 同样规范化，与 map_lines_to_units 中的 file_path 对齐
                            norm_key = row[1].replace("\\", "/")
                            units_by_file.setdefault(norm_key, []).append({
                                "qualified_name": row[0],
                                "start_line": row[2],
                                "end_line": row[3],
                            })

                    logger.info("[analysis] DB matched %s file(s) in code_units: %s",
                                len(units_by_file), list(units_by_file.keys()))

                    qualified_names = map_lines_to_units(changed_files, units_by_file)
                    logger.info("[analysis] map_lines_to_units → %s qn(s): %s",
                                len(qualified_names), qualified_names[:10])
                else:
                    qualified_names = []
                    logger.warning("[analysis] git diff returned 0 changed files for repo_id=%s "
                                   "(no staged/unstaged changes detected). "
                                   "If you committed the changes, use 'commit' mode instead of 'head'.",
                                   repo_id)

            if source_event:
                yield f"data: {json.dumps(source_event, ensure_ascii=False)}\n\n"

            yield f"data: {json.dumps({'type': 'changed_methods', 'methods': qualified_names}, ensure_ascii=False)}\n\n"

            if not qualified_names:
                logger.warning("[analysis] qualified_names is empty, stopping.")
                yield f"data: {json.dumps({'type': 'done', 'message': 'No changed methods found'})}\n\n"
                return

            # 构建影响链（含 call_line 字段）
            logger.info("[analysis] building impact chain for %s method(s)", len(qualified_names))
            impact = await get_impact_chain(qualified_names, repo_id, db)
            logger.info("[analysis] impact chain built: %s chain(s)", len(impact))
            yield f"data: {json.dumps({'type': 'impact', 'chains': impact}, ensure_ascii=False)}\n\n"

            # LLM 流式生成报告
            messages = get_impact_prompt(impact, lang)
            report_chunks: list[str] = []
            async for chunk in stream_chat(messages):
                report_chunks.append(chunk)
                yield f"data: {json.dumps({'type': 'chunk', 'text': chunk}, ensure_ascii=False)}\n\n"

            await db.execute(
                "INSERT INTO analysis_reports"
                "(repo_id, trigger_type, changed_units, impact_json, report_text) "
                "VALUES(?,?,?,?,?)",
                (repo_id, mode, json.dumps(qualified_names), json.dumps(impact),
                 "".join(report_chunks)),
            )
            await db.commit()
            yield f"data: {json.dumps({'type': 'done'})}\n\n"

        except Exception as e:
            logger.exception("[analysis] exception in analysis_stream: %s", e)
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
