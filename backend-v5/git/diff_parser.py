from __future__ import annotations
import re
from pathlib import Path
from typing import Any
import git
from config import SUPPORTED_EXTENSIONS


def _parse_diff_hunks(diff_text: str) -> list[tuple[int, int]]:
    """Extract changed line ranges from a unified diff hunk."""
    ranges = []
    for m in re.finditer(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", diff_text):
        start = int(m.group(1))
        count = int(m.group(2)) if m.group(2) is not None else 1
        ranges.append((start, start + count))
    return ranges


def get_diff_changed_files(
    repo_path: str,
    base: str = "HEAD",
    compare: str | None = None,
) -> list[dict[str, Any]]:
    """
    Returns list of changed files with their diff info.
    base/compare can be commit hashes, branch names, or 'HEAD'.
    compare=None means working tree vs base.
    """
    try:
        repo = git.Repo(repo_path)
    except git.InvalidGitRepositoryError:
        return []

    changed: list[dict[str, Any]] = []

    if compare is None:
        # staged + unstaged vs HEAD
        diffs = repo.index.diff(base) + repo.index.diff(None)
        # also untracked
    else:
        diffs = repo.commit(base).diff(repo.commit(compare))

    seen_paths: set[str] = set()
    for diff_item in diffs:
        path = diff_item.b_path or diff_item.a_path
        if not path or path in seen_paths:
            continue
        suffix = Path(path).suffix
        if suffix not in SUPPORTED_EXTENSIONS:
            continue
        seen_paths.add(path)
        try:
            diff_text = diff_item.diff.decode("utf-8", errors="replace") if diff_item.diff else ""
        except Exception:
            diff_text = ""
        changed.append({
            "file_path": path,
            "language": SUPPORTED_EXTENSIONS[suffix],
            "change_type": diff_item.change_type,
            "diff_text": diff_text[:3000],
            "line_ranges": _parse_diff_hunks(diff_text),
        })

    return changed


def get_diff_vs_head(repo_path: str) -> list[dict[str, Any]]:
    return get_diff_changed_files(repo_path, base="HEAD", compare=None)


def get_diff_between(repo_path: str, base: str, compare: str) -> list[dict[str, Any]]:
    return get_diff_changed_files(repo_path, base=base, compare=compare)


def map_lines_to_units(
    changed_files: list[dict], units_by_file: dict[str, list[dict]]
) -> list[str]:
    """
    Given changed line ranges and code units grouped by file,
    return list of qualified_names that overlap with changed lines.
    """
    affected: list[str] = []
    for cf in changed_files:
        fp = cf["file_path"].replace("\\", "/")
        units = units_by_file.get(fp, [])
        ranges = cf.get("line_ranges", [])
        if not ranges:
            # no range info — include all units in file
            affected.extend(u["qualified_name"] for u in units)
            continue
        for unit in units:
            s, e = unit.get("start_line", 0), unit.get("end_line", 0)
            for r_start, r_end in ranges:
                if s <= r_end and e >= r_start:
                    affected.append(unit["qualified_name"])
                    break
    return list(set(affected))
