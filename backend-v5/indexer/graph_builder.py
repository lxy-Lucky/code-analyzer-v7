from __future__ import annotations
import logging
from typing import Any
import aiosqlite

logger = logging.getLogger("graph_builder")

# 影响链 BFS 最多追踪的节点总数（防止大库调用图爆炸，保证响应速度）
_MAX_BFS_NODES = 200

# 太通用的方法名不做短名模糊匹配，否则会误命中大量无关调用
_SHORT_NAME_BLACKLIST = {
    "get", "set", "run", "call", "find", "save", "load", "send",
    "init", "start", "stop", "close", "open", "read", "write",
    "add", "put", "remove", "clear", "reset", "update", "create",
    "check", "is", "has", "log", "print", "format", "parse",
    "execute", "handle", "process", "build", "make", "convert",
}


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
    """
    按层批量 BFS，找所有上游调用者（最多 max_depth 层）。

    关键优化：把原来"每个节点一次 DB 查询"改为"每层一次批量 IN 查询"。
    查询次数从 O(节点总数) 降到 O(max_depth)，大库从数千次变成 3 次。
    """
    visited: dict[str, dict] = {}
    # 当前层待处理的节点集合
    current_level: set[str] = {start_qualified}

    for depth in range(max_depth + 1):
        if not current_level:
            break

        # 把本层所有新节点注册到 visited
        new_nodes = [qn for qn in current_level if qn not in visited]
        if not new_nodes:
            break
        for qn in new_nodes:
            visited[qn] = {"depth": depth, "callers": []}

        # 已达最大深度或节点上限，不再往下查
        if depth >= max_depth or len(visited) >= _MAX_BFS_NODES:
            break

        # 构建本层的 callee 查询模式：全限定名 + 短名（非黑名单）
        # callee_to_owners: callee 模式 → 它对应的本层节点列表
        callee_to_owners: dict[str, list[str]] = {}
        for qn in new_nodes:
            callee_to_owners.setdefault(qn, []).append(qn)
            short = qn.split("#")[-1] if "#" in qn else qn
            if short not in _SHORT_NAME_BLACKLIST and len(short) > 4:
                callee_to_owners.setdefault(short, []).append(qn)

        callee_values = list(callee_to_owners.keys())
        if not callee_values:
            break

        # 单次批量查询，覆盖本层所有节点
        ph = ",".join("?" * len(callee_values))
        next_level: set[str] = set()
        seen_pairs: set[tuple[str, str]] = set()

        logger.debug("[bfs] depth=%s nodes=%s callee_patterns=%s query_params=%s",
                     depth, len(new_nodes), len(callee_values), len(callee_values) + 1)

        async with db.execute(
            f"SELECT caller_qualified, callee_qualified, call_line "
            f"FROM call_edges WHERE repo_id=? AND callee_qualified IN ({ph})",
            [repo_id] + callee_values,
        ) as cur:
            async for row in cur:
                caller, callee, call_line = row[0], row[1], (row[2] or 0)
                for owner_qn in callee_to_owners.get(callee, []):
                    pair = (owner_qn, caller)
                    if pair in seen_pairs:
                        continue
                    seen_pairs.add(pair)
                    visited[owner_qn]["callers"].append({
                        "caller": caller, "call_line": call_line,
                    })
                    if caller not in visited and (len(visited) + len(next_level)) < _MAX_BFS_NODES:
                        next_level.add(caller)

        current_level = next_level

    logger.debug("[bfs] start=%s total_visited=%s", start_qualified, len(visited))
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
    logger.info("[impact] building chain for %s method(s), repo=%s",
                len(changed_qualified_names), repo_id)
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

    logger.info("[impact] done: %s chain(s), upstream nodes: %s",
                len(result), sum(len(r["impact_chain"]) for r in result))
    return result
