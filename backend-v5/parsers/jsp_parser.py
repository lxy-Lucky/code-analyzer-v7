from __future__ import annotations
import re
from pathlib import Path
from typing import Any

# ① 已移除无用的 tree-sitter import（Language/Parser/JAVA_LANG/_java_parser 从未被调用）

_RE_JSP_COMMENT  = re.compile(r"<%--.*?--%>", re.DOTALL)  # JSP 注释必须在 scriptlet 前排除
_RE_SCRIPTLET    = re.compile(r"<%(?!=|@|--)(.+?)%>", re.DOTALL)
# ③ 已知限制：非贪婪匹配在 scriptlet 内含 "%>" 字符串时会误截断（如 String s = "end%>";）
# 正则无法安全处理此边界情况，需状态机才能完全正确；当前实现对典型代码已足够
_RE_DECLARATION  = re.compile(r"<%!(.*?)%>", re.DOTALL)  # ② declaration 块（成员/方法声明）
_RE_EXPR         = re.compile(r"\$\{([^}]+)\}", re.DOTALL)
_RE_JSP_TAG      = re.compile(r"<([a-zA-Z]+:[a-zA-Z]+)([^>]*?)(?:/>|>)", re.DOTALL)
_RE_PAGE_DIRECTIVE = re.compile(r"<%@\s*page([^%]*)%>", re.DOTALL)
_RE_METHOD       = re.compile(
    r"(public|private|protected|static|void|int|String|boolean|long|Object|List|Map)"
    r"[^{;]*\([^)]*\)\s*\{",
    re.DOTALL,
)


def _decode_with_fallback(raw: bytes) -> str:
    """UTF-8 → CP932(Shift-JIS) → GB2312 → Latin-1 fallback。"""
    try:
        import chardet
        detected = chardet.detect(raw)
        enc = detected.get("encoding") or "utf-8"
        if enc.lower() == "ascii":
            enc = "utf-8"
        return raw.decode(enc, errors="replace")
    except Exception:
        pass
    for enc in ("utf-8", "cp932", "gb2312", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("latin-1", errors="replace")


_MIN_SCRIPTLET_LINES = 3    # 有效行数（去空行）少于此值的碎片不单独索引
_MERGE_LINE_GAP      = 30   # 两个 scriptlet 起始行差距在此范围内则合并


def _extract_scriptlets(content: str) -> list[dict[str, Any]]:
    """
    提取 scriptlet 并做两步过滤/合并：
    1. 收集所有非空 scriptlet，记录行号和代码
    2. 将行距 ≤ _MERGE_LINE_GAP 的相邻 scriptlet 合并为一个逻辑块
    3. 合并后代码行数 < _MIN_SCRIPTLET_LINES 的碎片丢弃
    目的：避免 `cel = "A"+(iRow);` 这类单行碎片占据向量搜索结果。
    """
    raw: list[tuple[int, int, str]] = []  # (start_line, end_line, code)
    for m in _RE_SCRIPTLET.finditer(content):
        code = m.group(1).strip()
        if not code:
            continue
        start = content[: m.start()].count("\n") + 1
        end   = start + code.count("\n")
        raw.append((start, end, code))

    if not raw:
        return []

    # 合并相邻碎片
    groups: list[tuple[int, int, list[str]]] = []
    g_start, g_end, g_codes = raw[0]
    g_codes = [raw[0][2]]
    for start, end, code in raw[1:]:
        if start - g_end <= _MERGE_LINE_GAP:
            g_end = max(g_end, end)
            g_codes.append(code)
        else:
            groups.append((g_start, g_end, g_codes))
            g_start, g_end, g_codes = start, end, [code]
    groups.append((g_start, g_end, g_codes))

    units = []
    for idx, (start_line, end_line, codes) in enumerate(groups):
        merged = "\n".join(codes)
        effective_lines = sum(1 for l in merged.splitlines() if l.strip())
        if effective_lines < _MIN_SCRIPTLET_LINES:
            continue
        summary = f"[JSP] scriptlet@line{start_line} | code: {merged[:80].replace(chr(10), ' ')}"
        units.append({
            "language": "jsp",
            "unit_type": "scriptlet",
            "qualified_name": f"scriptlet_{idx}_{start_line}",
            "name": f"scriptlet@line{start_line}",
            "signature": f"<% ... %> at line {start_line}-{end_line}",
            "start_line": start_line,
            "end_line": end_line,
            "body_text": merged[:2000],
            "calls": [],
            "summary": summary,
            "comment": "",
        })
    return units


def _extract_declarations(content: str) -> list[dict[str, Any]]:
    """② 提取 <%! declaration %> 块内的 Java 方法声明。"""
    units = []
    for i, m in enumerate(_RE_DECLARATION.finditer(content)):
        block = m.group(1).strip()
        if not block:
            continue
        block_line = content[: m.start()].count("\n") + 1
        for j, mm in enumerate(_RE_METHOD.finditer(block)):
            line_offset = block[:mm.start()].count("\n")
            line_no = block_line + line_offset
            # 提取到第一个 { 之前作为签名
            sig = mm.group(0).rstrip("{").strip()
            # 提取方法体
            body_start = mm.end() - 1
            depth = 0
            body_end = body_start
            for k, ch in enumerate(block[body_start:], body_start):
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        body_end = k + 1
                        break
            body = block[body_start:body_end]
            end_line = line_no + body.count("\n")
            # 从签名中提取方法名
            name_match = re.search(r'(\w+)\s*\(', sig)
            method_name = name_match.group(1) if name_match else f"declaration_{j}"
            qn = f"decl_{method_name}_{line_no}"
            units.append({
                "language": "jsp",
                "unit_type": "declaration",
                "qualified_name": qn,
                "name": method_name,
                "signature": sig[:200],
                "start_line": line_no,
                "end_line": end_line,
                "body_text": body[:2000],
                "calls": [],
                "summary": f"[JSP] declaration: {sig[:80]} at line {line_no}",
                "comment": "",
            })
    return units


def _extract_el_expressions(content: str) -> list[dict[str, Any]]:
    units = []
    seen: set[str] = set()
    for m in _RE_EXPR.finditer(content):
        expr = m.group(1).strip()
        if expr in seen:
            continue
        seen.add(expr)
        line_no = content[: m.start()].count("\n") + 1
        units.append({
            "language": "jsp",
            "unit_type": "el_expression",
            "qualified_name": f"el_{expr[:40].replace('.', '_').replace(' ', '_')}_{line_no}",
            "name": f"${{{expr}}}",
            "signature": f"${{expr}} at line {line_no}",
            "start_line": line_no,
            "end_line": line_no,
            "body_text": expr,
            "calls": [],
            "summary": f"[JSP] EL expression: ${{{expr}}} at line {line_no}",
            "comment": "",
        })
    return units


def _extract_custom_tags(content: str) -> list[dict[str, Any]]:
    units = []
    seen: set[str] = set()
    for m in _RE_JSP_TAG.finditer(content):
        tag_name = m.group(1)
        attrs_raw = m.group(2).strip()
        key = f"{tag_name}_{attrs_raw[:30]}"
        if key in seen:
            continue
        seen.add(key)
        line_no = content[: m.start()].count("\n") + 1
        attrs = dict(re.findall(r'(\w+)=["\']([^"\']*)["\']', attrs_raw))
        summary = f"[JSP] tag: <{tag_name}> at line {line_no}"
        if attrs:
            summary += f" | attrs: {', '.join(f'{k}={v}' for k, v in list(attrs.items())[:4])}"
        units.append({
            "language": "jsp",
            "unit_type": "custom_tag",
            "qualified_name": f"tag_{tag_name.replace(':', '_')}_{line_no}",
            "name": f"<{tag_name}>",
            "signature": f"<{tag_name} {attrs_raw[:60]}>",
            "start_line": line_no,
            "end_line": line_no,
            "body_text": m.group(0)[:500],
            "calls": [],
            "summary": summary,
            "comment": "",
        })
    return units


def parse_jsp(file_path: str) -> list[dict[str, Any]]:
    raw = Path(file_path).read_bytes()
    content = _decode_with_fallback(raw)
    # 先剥去 JSP 注释（<%-- ... --%>），避免注释内的 %> 干扰 scriptlet/declaration 正则
    content_stripped = _RE_JSP_COMMENT.sub("", content)
    units: list[dict[str, Any]] = []
    units.extend(_extract_declarations(content_stripped))
    units.extend(_extract_scriptlets(content_stripped))
    # custom_tag（<html:image>/<bean:write> 等）和 el_expression（${...}）
    # 信息量极低且数量极多，会在向量空间里淹没真正的业务逻辑，不再索引

    # deduplicate qualified names with counter
    seen_qnames: dict[str, int] = {}
    deduped = []
    for u in units:
        qn = u["qualified_name"]
        if qn in seen_qnames:
            seen_qnames[qn] += 1
            u["qualified_name"] = f"{qn}_{seen_qnames[qn]}"
        else:
            seen_qnames[qn] = 0
        deduped.append(u)
    return deduped
