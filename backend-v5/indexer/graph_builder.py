from __future__ import annotations
from collections import deque
from typing import Any
import aiosqlite


async def build_graph(repo_id: int, db: aiosqlite.Connection) -> dict[str, list[str]]:
    graph: dict[str, list[str]] = {}
    async with db.execute(
        "SELECT caller_qualified, callee_qualified FROM call_edges WHERE repo_id=?",
        (repo_id,),
    ) as cur:
        async for row in cur:
            caller, callee = row[0], row[1]
            graph.setdefault(callee, []).append(caller)
    return graph


async def bfs_upstream(
    start_qualified: str,
    repo_id: int,
    db: aiosqlite.Connection,
    max_depth: int = 3,
) -> dict[str, dict]:
    """BFS upstream: find all callers up to max_depth."""
    visited: dict[str, dict] = {}
    queue: deque = deque([(start_qualified, 0)])

    while queue:
        current, depth = queue.popleft()
        if depth > max_depth or current in visited:
            continue
        visited[current] = {"depth": depth, "callers": []}

        # callee_qualified 可能是短名(如 selectList)或全限定名(如 SysUserServiceImpl#selectList)
        # 用短名做模糊匹配覆盖跨类调用，但跳过过于通用的名字防止误命中
        _SHORT_NAME_BLACKLIST = {
            "get", "set", "run", "call", "find", "save", "load", "send",
            "init", "start", "stop", "close", "open", "read", "write",
            "add", "put", "remove", "clear", "reset", "update", "create",
            "check", "is", "has", "log", "print", "format", "parse",
            "execute", "handle", "process", "build", "make", "convert",
        }
        short_name = current.split("#")[-1] if "#" in current else current
        use_short_match = (
            short_name not in _SHORT_NAME_BLACKLIST
            and len(short_name) > 4
        )
        seen_callers: set[str] = set()
        if use_short_match:
            query_sql = (
                "SELECT caller_qualified, call_line FROM call_edges "
                "WHERE repo_id=? AND (callee_qualified=? OR callee_qualified=?)"
            )
            query_params = (repo_id, current, short_name)
        else:
            query_sql = (
                "SELECT caller_qualified, call_line FROM call_edges "
                "WHERE repo_id=? AND callee_qualified=?"
            )
            query_params = (repo_id, current)
        async with db.execute(query_sql, query_params) as cur:
            async for row in cur:
                caller, call_line = row[0], (row[1] or 0)
                if caller not in seen_callers:
                    seen_callers.add(caller)
                    visited[current]["callers"].append({"caller": caller, "call_line": call_line})
                    if caller not in visited:
                        queue.append((caller, depth + 1))

    return visited


async def get_unit_detail(
    qualified_name: str, repo_id: int, db: aiosqlite.Connection
) -> dict | None:
    async with db.execute(
        """SELECT qualified_name, name, file_path, start_line, end_line,
                  signature, body_text, language
           FROM code_units WHERE repo_id=? AND qualified_name=?""",
        (repo_id, qualified_name),
    ) as cur:
        row = await cur.fetchone()
        if row:
            return dict(row)
    return None


async def get_impact_chain(
    changed_qualified_names: list[str],
    repo_id: int,
    db: aiosqlite.Connection,
    max_depth: int = 3,
) -> list[dict[str, Any]]:
    """
    Build impact chain for each changed method.
    Each node includes call_line: the line in the caller where the changed method is called.
    """
    result = []
    for qn in changed_qualified_names:
        upstream = await bfs_upstream(qn, repo_id, db, max_depth)
        chain_nodes = []

        uqns = [uqn for uqn in upstream if uqn != qn]
        short_qn = qn.split("#")[-1] if "#" in qn else qn

        # 批量查 unit 详情，避免 N 次单条查询
        details: dict[str, dict] = {}
        if uqns:
            _ph = ",".join("?" * len(uqns))
            async with db.execute(
                f"SELECT qualified_name, file_path, start_line, end_line, "
                f"signature, body_text, language "
                f"FROM code_units WHERE repo_id=? AND qualified_name IN ({_ph})",
                [repo_id] + uqns,
            ) as cur:
                async for row in cur:
                    details[row[0]] = {
                        "file_path": row[1], "start_line": row[2], "end_line": row[3],
                        "signature": row[4], "body_text": row[5], "language": row[6],
                    }

        # 批量查 call_line，避免 N 次单条查询
        call_lines_map: dict[str, int] = {}
        if uqns:
            _ph = ",".join("?" * len(uqns))
            async with db.execute(
                f"SELECT caller_qualified, call_line FROM call_edges "
                f"WHERE repo_id=? AND caller_qualified IN ({_ph}) "
                f"AND (callee_qualified=? OR callee_qualified=?)",
                [repo_id] + uqns + [qn, short_qn],
            ) as cur:
                async for row in cur:
                    call_lines_map[row[0]] = row[1] or 0

        for uqn, info in upstream.items():
            if uqn == qn:
                continue
            detail = details.get(uqn)
            call_line = call_lines_map.get(uqn, 0)
            chain_nodes.append({
                "qualified_name": uqn,
                "depth": info["depth"],
                "file_path": detail["file_path"] if detail else "",
                "start_line": detail["start_line"] if detail else 0,
                "call_line": call_line,
                "signature": detail["signature"] if detail else uqn,
                "language": detail["language"] if detail else "",
                "body_text": (detail["body_text"] or "")[:400] if detail else "",
            })

        chain_nodes.sort(key=lambda x: x["depth"])
        changed_detail = await get_unit_detail(qn, repo_id, db)
        result.append({
            "changed": {
                "qualified_name": qn,
                "file_path": changed_detail["file_path"] if changed_detail else "",
                "start_line": changed_detail["start_line"] if changed_detail else 0,
                "end_line": changed_detail["end_line"] if changed_detail else 0,
                "signature": changed_detail["signature"] if changed_detail else qn,
                "body_text": (changed_detail["body_text"] or "")[:600] if changed_detail else "",
            },
            "impact_chain": chain_nodes,
        })
    return result
