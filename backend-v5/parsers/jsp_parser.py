from __future__ import annotations
import re
from pathlib import Path
from typing import Any
import tree_sitter_java as tsjava
from tree_sitter import Language, Parser

JAVA_LANG = Language(tsjava.language())
_java_parser = Parser(JAVA_LANG)

_RE_SCRIPTLET = re.compile(r"<%(?!=|@)(.*?)%>", re.DOTALL)
_RE_EXPR = re.compile(r"\$\{([^}]+)\}", re.DOTALL)
_RE_JSP_TAG = re.compile(r"<([a-zA-Z]+:[a-zA-Z]+)([^>]*?)(?:/>|>)", re.DOTALL)
_RE_PAGE_DIRECTIVE = re.compile(r"<%@\s*page([^%]*)%>", re.DOTALL)
_RE_METHOD = re.compile(
    r"(public|private|protected|static|void|int|String|boolean|long|Object|List|Map)[^{;]*\([^)]*\)\s*\{",
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


def _extract_scriptlets(content: str) -> list[dict[str, Any]]:
    units = []
    for i, m in enumerate(_RE_SCRIPTLET.finditer(content)):
        code = m.group(1).strip()
        if not code:
            continue
        line_no = content[: m.start()].count("\n") + 1
        summary = f"[JSP] scriptlet@line{line_no} | code: {code[:80].replace(chr(10), ' ')}"
        units.append({
            "language": "jsp",
            "unit_type": "scriptlet",
            "qualified_name": f"scriptlet_{i}_{line_no}",
            "name": f"scriptlet@line{line_no}",
            "signature": f"<% ... %> at line {line_no}",
            "start_line": line_no,
            "end_line": line_no + code.count("\n"),
            "body_text": code[:2000],
            "calls": [],
            "summary": summary,
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
    units: list[dict[str, Any]] = []
    units.extend(_extract_scriptlets(content))
    units.extend(_extract_el_expressions(content))
    units.extend(_extract_custom_tags(content))

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
