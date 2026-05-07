from __future__ import annotations
import re
from pathlib import Path
from typing import Any
import tree_sitter_javascript as tsjs
from tree_sitter import Language, Parser, Node

JS_LANG = Language(tsjs.language())
_parser = Parser(JS_LANG)


def _detect_encoding(raw: bytes) -> str:
    """检测文件编码，返回编码名称字符串。ASCII 升级为 utf-8。"""
    for encoding in ("utf-8", "cp932", "gb2312", "cp1252", "latin-1"):
        try:
            raw.decode(encoding)
            return encoding
        except (UnicodeDecodeError, LookupError):
            continue
    return "latin-1"


def _decode_bytes(raw: bytes, enc: str) -> str:
    try:
        return raw.decode(enc, errors="replace")
    except (UnicodeDecodeError, LookupError):
        return raw.decode("latin-1", errors="replace")


def _text(node: Node, src: bytes, enc: str) -> str:
    """从 tree-sitter 节点切出字节段，用文件实际编码解码。"""
    return _decode_bytes(src[node.start_byte:node.end_byte], enc)


def _first_child(node: Node, *types: str) -> Node | None:
    for c in node.children:
        if c.type in types:
            return c
    return None


def _extract_calls(node: Node, src: bytes, enc: str) -> list[tuple[str, int]]:
    """返回 (callee_name, call_line) 列表，同一位置只记录一次。"""
    calls: list[tuple[str, int]] = []
    seen: set[str] = set()
    queue = list(node.children)
    while queue:
        n = queue.pop()
        if n.type == "call_expression":
            fn = _first_child(n, "identifier", "member_expression")
            if fn:
                name = _text(fn, src, enc).split("\n")[0][:60]
                line = n.start_point[0] + 1
                key = f"{name}:{line}"
                if key not in seen:
                    seen.add(key)
                    calls.append((name, line))
        queue.extend(n.children)
    return calls


def _extract_comment(node: Node, src: bytes, enc: str) -> str:
    prev = node.prev_sibling
    if prev and prev.type == "comment":
        return _decode_bytes(
            src[prev.start_byte:prev.end_byte], enc
        ).lstrip("/").lstrip("*").strip()
    return ""


def _extract_param_names(params_node: Node | None, src: bytes, enc: str) -> list[str]:
    if params_node is None:
        return []
    names: list[str] = []
    for child in params_node.children:
        if child.type == "identifier":
            names.append(_text(child, src, enc))
        elif child.type in ("assignment_pattern", "rest_pattern"):
            id_node = _first_child(child, "identifier")
            if id_node:
                names.append(_text(id_node, src, enc))
    return names


def _handle_function(
    node: Node, src: bytes, enc: str, parent_name: str = ""
) -> dict[str, Any] | None:
    name = ""
    if node.type == "function_declaration":
        id_node = _first_child(node, "identifier")
        name = _text(id_node, src, enc) if id_node else "anonymous"
    elif node.type in ("arrow_function", "function_expression"):
        name = parent_name or "anonymous"
    elif node.type == "method_definition":
        id_node = _first_child(node, "property_identifier", "identifier")
        name = _text(id_node, src, enc) if id_node else "method"

    params_node = _first_child(node, "formal_parameters")
    params = _text(params_node, src, enc) if params_node else "()"
    param_names = _extract_param_names(params_node, src, enc)

    body_node = _first_child(node, "statement_block", "expression")
    body = _text(body_node, src, enc)[:2000] if body_node else ""
    call_tuples: list[tuple[str, int]] = _extract_calls(body_node, src, enc) if body_node else []
    calls = [c[0] for c in call_tuples]
    comment = _extract_comment(node, src, enc)

    qualified = f"{parent_name}.{name}" if parent_name else name
    name_words = re.sub(r"([A-Z])", r" \1", name).strip()
    class_words = re.sub(r"([A-Z])", r" \1", parent_name).strip() if parent_name else ""
    summary = (
        f"[JS] {'class ' + class_words + ' ' if class_words else ''}"
        f"method {name_words} | full: {qualified}{params}"
    )
    if param_names:
        summary += f" | params: {', '.join(param_names)}"
    if calls:
        summary += f" | calls: {', '.join(calls[:5])}"
    if name.startswith("set"):
        summary += f" | setter for {name[3:]}"
    elif name.startswith("get"):
        summary += f" | getter for {name[3:]}"
    elif name.startswith(("is", "has", "check")):
        summary += f" | boolean check: {name_words}"

    return {
        "language": "javascript",
        "unit_type": "function",
        "qualified_name": qualified,
        "name": name,
        "class_name": parent_name or "",
        "signature": f"function {name}{params}",
        "start_line": node.start_point[0] + 1,
        "end_line": node.end_point[0] + 1,
        "body_text": body,
        "calls": calls,
        "call_lines": call_tuples,
        "comment": comment,
        "summary": summary,
        "param_names": param_names,
        "param_types": [],
        "return_type": "",
    }


def _unique_qn(qn: str, seen: set[str], line: int) -> str:
    if qn not in seen:
        return qn
    candidate = f"{qn}_L{line}"
    counter = 1
    while candidate in seen:
        candidate = f"{qn}_L{line}_{counter}"
        counter += 1
    return candidate


def parse_js(file_path: str) -> list[dict[str, Any]]:
    src_bytes = Path(file_path).read_bytes()
    enc = _detect_encoding(src_bytes)          # 整个文件只检测一次
    tree = _parser.parse(src_bytes)
    root = tree.root_node
    units: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(unit: dict, line: int):
        unit["qualified_name"] = _unique_qn(unit["qualified_name"], seen, line)
        seen.add(unit["qualified_name"])
        units.append(unit)

    def walk(node: Node, class_name: str = ""):
        if node.type in ("function_declaration", "method_definition"):
            unit = _handle_function(node, src_bytes, enc, class_name)
            if unit:
                _add(unit, node.start_point[0])
        elif node.type == "class_declaration":
            id_node = _first_child(node, "identifier")
            cname = _text(id_node, src_bytes, enc) if id_node else "AnonymousClass"
            for child in node.children:
                walk(child, cname)
            return
        elif node.type == "lexical_declaration":
            for child in node.children:
                if child.type == "variable_declarator":
                    id_n = _first_child(child, "identifier")
                    val_n = _first_child(child, "arrow_function", "function_expression")
                    if id_n and val_n:
                        vname = _text(id_n, src_bytes, enc)
                        unit = _handle_function(val_n, src_bytes, enc, vname)
                        if unit:
                            _add(unit, val_n.start_point[0])
        for child in node.children:
            walk(child, class_name)

    walk(root)
    return units
