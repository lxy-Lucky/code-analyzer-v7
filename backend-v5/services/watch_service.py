from __future__ import annotations
import gc
from pathlib import Path
from typing import Callable

from core.db import get_db
from core.events import broadcast
from indexer.scanner import get_changed_files_units
from indexer.graph_builder import get_impact_chain

# repo_id -> {qualified_name: {file_path, name}}
_watched_changes: dict[int, dict[str, dict]] = {}


def get_changes(repo_id: int) -> dict[str, dict]:
    return _watched_changes.get(repo_id, {})


def clear_changes(repo_id: int) -> None:
    _watched_changes.pop(repo_id, None)


def make_on_change(repo_id: int, repo_path: str) -> Callable:
    async def on_change(paths: list[str]) -> None:
        async with get_db() as db:
            try:
                rel_paths = [str(Path(p).relative_to(repo_path)) for p in paths]
                changed_units = await get_changed_files_units(repo_id, paths, repo_path, db)
                await broadcast({
                    "type": "index_updated",
                    "repo_id": repo_id,
                    "files": rel_paths,
                    "units_updated": len(changed_units),
                })

                if not changed_units:
                    return

                _watched_changes.setdefault(repo_id, {})
                for u in changed_units:
                    _watched_changes[repo_id][u["qualified_name"]] = {
                        "file_path": u.get("file_path", ""),
                        "name": u.get("name", ""),
                    }

                qnames = list(_watched_changes[repo_id].keys())
                impact = await get_impact_chain(qnames, repo_id, db)
                await broadcast({
                    "type": "watch_analysis",
                    "repo_id": repo_id,
                    "files": rel_paths,
                    "changed_methods": qnames,
                    "impact": impact,
                })
            except Exception as e:
                await broadcast({
                    "type": "watch_analysis_error",
                    "repo_id": repo_id,
                    "error": str(e),
                })
            finally:
                gc.collect()

    return on_change
