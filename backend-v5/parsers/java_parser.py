from __future__ import annotations
import re
from pathlib import Path
from typing import Any
import tree_sitter_java as tsjava
from tree_sitter import Language, Parser, Node

JAVA_LANG = Language(tsjava.language())
_parser = Parser(JAVA_LANG)


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
    """用指定编码 decode，失败则 latin-1 兜底。"""
    try:
        return raw.decode(enc, errors="replace")
    except (UnicodeDecodeError, LookupError):
        return raw.decode("latin-1", errors="replace")


def _text(node: Node, src: bytes, enc: str) -> str:
    """从 tree-sitter 节点切出字节段，用文件实际编码解码。"""
    return _decode_bytes(src[node.start_byte:node.end_byte], enc)


def _find_children(node: Node, *types: str) -> list[Node]:
    return [c for c in node.children if c.type in types]


def _first_child(node: Node, *types: str) -> Node | None:
    for c in node.children:
        if c.type in types:
            return c
    return None


def _extract_javadoc(node: Node, src: bytes, enc: str) -> str:
    """提取紧邻 node 之前的 javadoc 注释。"""
    prev = node.prev_sibling
    while prev and prev.type in ("line_comment", "block_comment"):
        text = _decode_bytes(src[prev.start_byte:prev.end_byte], enc).strip()
        if text.startswith("/**"):
            clean = re.sub(r"/\*\*|\*/|^\s*\*\s?", "", text, flags=re.MULTILINE)
            return " ".join(clean.split())
        prev = prev.prev_sibling
    return ""


def _get_package(tree_root: Node, src: bytes, enc: str) -> str:
    for child in tree_root.children:
        if child.type == "package_declaration":
            for c in child.children:
                if c.type in ("scoped_identifier", "identifier"):
                    return _text(c, src, enc)
    return ""


def _extract_class_name(node: Node, src: bytes, enc: str) -> str:
    for c in node.children:
        if c.type == "identifier":
            return _text(c, src, enc)
    return "Unknown"


def _extract_calls(body_node: Node, src: bytes, enc: str) -> list[tuple[str, int]]:
    calls: list[tuple[str, int]] = []
    seen: set[str] = set()
    queue = list(body_node.children)
    while queue:
        n = queue.pop()
        if n.type == "method_invocation":
            method_name_nodes = [c for c in n.children if c.type == "identifier"]
            if method_name_nodes:
                name = _text(method_name_nodes[-1], src, enc)
                line = n.start_point[0] + 1
                key = f"{name}:{line}"
                if key not in seen:
                    seen.add(key)
                    calls.append((name, line))
        queue.extend(n.children)
    return calls


def _modifiers(node: Node, src: bytes, enc: str) -> list[str]:
    mods = []
    for c in node.children:
        if c.type == "modifiers":
            for m in c.children:
                if m.type not in ("{", "}", "@"):
                    mods.append(_text(m, src, enc))
    return mods


def _extract_params(
    params_node: Node | None, src: bytes, enc: str
) -> tuple[list[str], list[str]]:
    """返回 (param_names, param_types)。"""
    if params_node is None:
        return [], []
    names: list[str] = []
    types: list[str] = []
    for child in params_node.children:
        if child.type in ("formal_parameter", "spread_parameter"):
            type_node = None
            name_node = None
            for c in child.children:
                if c.type in (
                    "type_identifier", "integral_type", "boolean_type",
                    "void_type", "floating_point_type", "generic_type",
                    "array_type", "scoped_type_identifier",
                ):
                    type_node = c
                elif c.type == "identifier":
                    name_node = c
            if type_node:
                types.append(_text(type_node, src, enc))
            if name_node:
                names.append(_text(name_node, src, enc))
    return names, types


def _strip_large_array_initializer(src: bytes, enc: str) -> bytes:
    text = src.decode(enc, errors="replace")

    # ⑯ 泛化为匹配任意基本类型的多维数组大初始化器，不只是 char[][][]
    pattern = re.compile(
        r'((?:private|public|protected)?\s+static\s+final\s+'
        r'(?:char|byte|int|long|short|boolean|String)\s*(?:\[\]\s*)+'
        r'\w+\s*=\s*(?:new\s+(?:char|byte|int|long|short|boolean|String)\s*(?:\[\]\s*)+)?\{)',
        re.DOTALL
    )

    m = pattern.search(text)
    if not m:
        return src

    # 找 { ... }
    start = m.end() - 1
    depth = 0
    end = -1

    for i in range(start, len(text)):
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    if end == -1:
        # 括号未闭合，无法安全替换，直接返回原始字节
        return src

    # 找分号
    semi_match = re.search(r';', text[end:])
    if not semi_match:
        return src
    semi = end + semi_match.start()

    header = text[m.start():m.end() - 1]

    new_text = text[:m.start()] + header + "{};" + text[semi + 1:]

    return new_text.encode(enc, errors="replace")


def parse_java(file_path: str) -> list[dict[str, Any]]:
    src_bytes = Path(file_path).read_bytes()
    enc = _detect_encoding(src_bytes)          # 整个文件只检测一次
    src_bytes = _strip_large_array_initializer(src_bytes, enc)
    tree = _parser.parse(src_bytes)
    root = tree.root_node
    package = _get_package(root, src_bytes, enc)
    units: list[dict[str, Any]] = []

    def walk_class(class_node: Node, class_name: str):
        qualified_class = f"{package}.{class_name}" if package else class_name
        for child in class_node.children:
            if child.type in ("class_body", "interface_body", "enum_body"):
                for member in child.children:
                    if member.type == "method_declaration":
                        _handle_method(member, qualified_class, class_name)
                    elif member.type == "constructor_declaration":
                        _handle_method(member, qualified_class, class_name, is_constructor=True)
                    elif member.type in (
                        "class_declaration", "interface_declaration", "enum_declaration"
                    ):
                        inner_name = _extract_class_name(member, src_bytes, enc)
                        walk_class(member, f"{class_name}.{inner_name}")
                    elif member.type == "ERROR":
                        # tree-sitter 误解析（常见于大型静态数组初始化后），
                        # 用正则从 ERROR 节点的文本里补充提取方法声明
                        _recover_methods_from_error(member, qualified_class, class_name)


    _RE_METHOD_SIG = re.compile(
        r'(?:(?:public|protected|private|static|final|synchronized|abstract|native)'
        r'(?:\s+(?:public|protected|private|static|final|synchronized|abstract|native))*\s+)?'
        r'(?:<[^>]*>\s*)?'                          
        # 泛型
        r'(\w[\w\[\]<>, ]*?)\s+'                    # 返回类型
        r'(\w+)\s*'                                 # 方法名
        r'\(([^)]*)\)\s*'                           # 参数
        r'(?:throws\s+[\w\s,]+?)?\s*\{',            # throws + 开括号
        re.DOTALL,
    )

    def _recover_methods_from_error(error_node: Node, qualified_class: str, class_name: str):
        """从 tree-sitter ERROR 节点的原始文本里用正则补充提取方法。"""
        raw = _decode_bytes(src_bytes[error_node.start_byte:error_node.end_byte], enc)
        start_line_offset = error_node.start_point[0]
        for m in _RE_METHOD_SIG.finditer(raw):
            ret_type   = m.group(1).strip()
            method_name = m.group(2).strip()
            if method_name in _JAVA_KEYWORDS:
                continue
            params_text = f"({m.group(3).strip()})"
            qualified_name = f"{qualified_class}#{method_name}"
            # 估算行号
            start_line = start_line_offset + raw[:m.start()].count("\n") + 1
            # 提取方法体（从 { 到对应的 }）
            body_start = m.end() - 1   # 指向 {
            depth = 0
            body_end = body_start
            for i, ch in enumerate(raw[body_start:], body_start):
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        body_end = i + 1
                        break
            body_text = raw[body_start:body_end]
            end_line = start_line + body_text.count("\n")
            mods_match = re.match(
                r'((?:(?:public|protected|private|static|final|synchronized|abstract|native)\s+)*)',
                raw[max(0, m.start()-80):m.start()]
            )
            mods = mods_match.group(1).strip() if mods_match else ""
            signature = f"{mods} {ret_type} {method_name}{params_text}".strip()
            # 提取 javadoc（方法签名前的 /** ... */ 注释）
            preceding = raw[max(0, m.start()-500):m.start()]
            jd_match = re.search(r'/\*\*(.*?)\*/', preceding, re.DOTALL)
            javadoc = ""
            if jd_match:
                javadoc = re.sub(r'\s*\*\s*', ' ', jd_match.group(1)).strip()

            name_words = re.sub(r"([A-Z])", r" \1", method_name).strip()
            class_words = re.sub(r"([A-Z])", r" \1", class_name).strip()

            # 从 body_text 用正则提取方法调用，弥补 tree-sitter ERROR 节点调用信息丢失
            _call_re = re.compile(r'\b([a-zA-Z_]\w*)\s*\(')
            _recovered_calls: list[tuple[str, int]] = []
            _seen_calls: set[str] = set()
            for _cm in _call_re.finditer(body_text):
                _cname = _cm.group(1)
                if _cname in _JAVA_KEYWORDS or len(_cname) <= 2 or _cname == method_name:
                    continue
                _cline = start_line + body_text[:_cm.start()].count("\n")
                _key = f"{_cname}:{_cline}"
                if _key not in _seen_calls:
                    _seen_calls.add(_key)
                    _recovered_calls.append((_cname, _cline))

            summary = (
                f"[Java] class {class_words} method {name_words} | "
                f"full: {class_name}.{method_name}{params_text} | "
                f"returns: {ret_type}"
            )
            if javadoc:
                summary += f" | doc: {javadoc[:200]}"
            if _recovered_calls:
                summary += f" | calls: {', '.join(c[0] for c in _recovered_calls[:5])}"

            units.append({
                "language": "java",
                "unit_type": "method",
                "qualified_name": qualified_name,
                "name": method_name,
                "class_name": class_name,
                "signature": signature,
                "start_line": start_line,
                "end_line": end_line,
                "body_text": body_text[:2000],
                "calls": [c[0] for c in _recovered_calls],
                "call_lines": _recovered_calls,
                "summary": summary,
                "comment": javadoc,
                "param_names": [],
                "param_types": [],
                "return_type": ret_type,
            })

    # Java 关键字集合，用于过滤 tree-sitter 误解析的节点
    _JAVA_KEYWORDS = {
        "catch", "try", "finally", "if", "else", "for", "while", "do",
        "switch", "case", "return", "throw", "new", "this", "super",
        "class", "interface", "enum", "extends", "implements",
        "import", "package", "static", "final", "abstract", "synchronized",
        "volatile", "transient", "native", "strictfp", "assert", "break",
        "continue", "default", "instanceof", "void", "null", "true", "false",
    }

    def _handle_method(
        node: Node, qualified_class: str, class_name: str, is_constructor: bool = False
    ):
        name_node = _first_child(node, "identifier")
        if not name_node:
            return
        method_name = _text(name_node, src_bytes, enc)

        # tree-sitter 误解析时可能把 catch/try 等关键字当成方法名，直接跳过
        if method_name in _JAVA_KEYWORDS:
            return

        params_node = _first_child(node, "formal_parameters")
        params_text = _text(params_node, src_bytes, enc) if params_node else "()"
        param_names, param_types = _extract_params(params_node, src_bytes, enc)

        body_node = _first_child(node, "block")
        body_text = _text(body_node, src_bytes, enc) if body_node else ""
        call_tuples = _extract_calls(body_node, src_bytes, enc) if body_node else []
        calls = [c[0] for c in call_tuples]

        mods = _modifiers(node, src_bytes, enc)
        ret_node = _first_child(
            node, "void_type", "type_identifier", "integral_type",
            "generic_type", "array_type", "boolean_type", "floating_point_type",
        )
        return_type = _text(ret_node, src_bytes, enc) if ret_node else (
            "void" if is_constructor else ""
        )

        javadoc = _extract_javadoc(node, src_bytes, enc)
        qualified_name = f"{qualified_class}#{method_name}"
        signature = f"{' '.join(mods)} {return_type} {method_name}{params_text}".strip()

        name_words = re.sub(r"([A-Z])", r" \1", method_name).strip()
        class_words = re.sub(r"([A-Z])", r" \1", class_name).strip()

        summary_parts = [
            f"[Java] class {class_words} method {name_words}",
            f"full: {class_name}.{method_name}{params_text}",
        ]
        if param_types:
            summary_parts.append(f"param types: {' '.join(param_types)}")
        if return_type and return_type != "void":
            summary_parts.append(f"returns: {return_type}")
        if javadoc:
            summary_parts.append(f"doc: {javadoc[:200]}")
        if calls:
            summary_parts.append(f"calls: {', '.join(calls[:5])}")
        if method_name.startswith("set"):
            summary_parts.append(f"setter for {method_name[3:]}")
        elif method_name.startswith("get"):
            summary_parts.append(f"getter for {method_name[3:]}")
        elif method_name.startswith(("is", "has", "check")):
            summary_parts.append(f"boolean check: {name_words}")
        elif method_name.startswith(("validate", "verify")):
            summary_parts.append(f"validation: {name_words}")

        units.append({
            "language": "java",
            "unit_type": "constructor" if is_constructor else "method",
            "qualified_name": qualified_name,
            "name": method_name,
            "class_name": class_name,
            "signature": signature,
            "start_line": node.start_point[0] + 1,
            "end_line": node.end_point[0] + 1,
            "body_text": body_text[:2000],
            "calls": calls,
            "call_lines": call_tuples,
            "summary": " | ".join(summary_parts),
            "comment": javadoc,
            "param_names": param_names,
            "param_types": param_types,
            "return_type": return_type,
        })

    for child in root.children:
        if child.type in ("class_declaration", "interface_declaration", "enum_declaration"):
            class_name = _extract_class_name(child, src_bytes, enc)
            walk_class(child, class_name)

    return units
