from __future__ import annotations
from pathlib import Path
from typing import Any
from lxml import etree


def _detect_xml_type(root: etree._Element) -> str:
    # lxml recover=True 会保留 Comment/PI 节点，其 tag 是 callable 而非 str
    if not isinstance(root.tag, str):
        return "generic"
    tag = root.tag.lower()
    ns = root.nsmap or {}
    attribs = {k.lower(): v for k, v in root.attrib.items()}

    if "mapper" in tag:
        return "mybatis_mapper"
    if tag in ("beans", "applicationcontext") or any("springframework" in str(v) for v in ns.values()):
        return "spring_beans"
    if tag == "project" and root.find("groupId") is not None:
        return "maven_pom"
    if tag == "web-app":
        return "web_xml"
    if "configuration" in tag:
        return "config"
    return "generic"


def _parse_mybatis(root: etree._Element, ns_prefix: str) -> list[dict[str, Any]]:
    units = []
    namespace = root.get("namespace", "")
    sql_tags = ("select", "insert", "update", "delete")
    for elem in root:
        if not isinstance(elem.tag, str):
            continue
        local = etree.QName(elem.tag).localname.lower()
        if local in sql_tags:
            stmt_id = elem.get("id", "unknown")
            qualified = f"{namespace}.{stmt_id}" if namespace else stmt_id
            sql_text = (elem.text or "").strip()[:500]
            param_type = elem.get("parameterType", "")
            result_type = elem.get("resultType", elem.get("resultMap", ""))
            summary = (
                f"[XML/MyBatis] {local.upper()} {stmt_id}"
                + (f" | param: {param_type}" if param_type else "")
                + (f" | result: {result_type}" if result_type else "")
                + (f" | sql: {sql_text[:60]}" if sql_text else "")
            )
            units.append({
                "language": "xml",
                "unit_type": f"mybatis_{local}",
                "qualified_name": qualified,
                "name": stmt_id,
                "signature": f"{local.upper()} {qualified}",
                "start_line": getattr(elem, "sourceline", 0),
                "end_line": getattr(elem, "sourceline", 0),
                "body_text": sql_text,
                "calls": [],
                "summary": summary,
                "comment": "",
            })
    return units


def _parse_spring_beans(root: etree._Element) -> list[dict[str, Any]]:
    units = []
    for elem in root.iter():
        if not isinstance(elem.tag, str):
            continue
        local = etree.QName(elem.tag).localname.lower()
        if local == "bean":
            bean_id = elem.get("id") or elem.get("name", "anonymous")
            class_name = elem.get("class", "")
            scope = elem.get("scope", "singleton")
            refs = [c.get("ref", "") for c in elem
                    if isinstance(c.tag, str) and etree.QName(c.tag).localname == "property" and c.get("ref")]
            summary = f"[XML/Spring] bean: {bean_id} | class: {class_name} | scope: {scope}"
            if refs:
                summary += f" | refs: {', '.join(refs[:4])}"
            units.append({
                "language": "xml",
                "unit_type": "spring_bean",
                "qualified_name": f"bean.{bean_id}",
                "name": bean_id,
                "signature": f"<bean id='{bean_id}' class='{class_name}'>",
                "start_line": getattr(elem, "sourceline", 0),
                "end_line": getattr(elem, "sourceline", 0),
                "body_text": class_name,
                "calls": refs,
                "summary": summary,
                "comment": "",
            })
    return units


def _parse_maven_pom(root: etree._Element) -> list[dict[str, Any]]:
    units = []
    ns = {"m": "http://maven.apache.org/POM/4.0.0"}

    def _find(tag: str) -> str:
        el = root.find(f"m:{tag}", ns) or root.find(tag)
        return el.text.strip() if el is not None and el.text else ""

    group = _find("groupId")
    artifact = _find("artifactId")
    version = _find("version")
    if artifact:
        units.append({
            "language": "xml",
            "unit_type": "pom_module",
            "qualified_name": f"{group}.{artifact}",
            "name": artifact,
            "signature": f"{group}:{artifact}:{version}",
            "start_line": 1,
            "end_line": 1,
            "body_text": f"{group}:{artifact}:{version}",
            "calls": [],
            "summary": f"[XML/Maven] module: {artifact} | group: {group} | version: {version}",
            "comment": "",
        })

    deps_root = root.find("m:dependencies", ns) or root.find("dependencies")
    if deps_root is not None:
        for dep in deps_root:
            dg = (dep.find("m:groupId", ns) or dep.find("groupId"))
            da = (dep.find("m:artifactId", ns) or dep.find("artifactId"))
            dv = (dep.find("m:version", ns) or dep.find("version"))
            if dg is None or da is None:
                continue
            dg_t = dg.text.strip() if dg.text else ""
            da_t = da.text.strip() if da.text else ""
            dv_t = dv.text.strip() if dv is not None and dv.text else ""
            units.append({
                "language": "xml",
                "unit_type": "pom_dependency",
                "qualified_name": f"dep.{dg_t}.{da_t}",
                "name": da_t,
                "signature": f"{dg_t}:{da_t}:{dv_t}",
                "start_line": getattr(dep, "sourceline", 0),
                "end_line": getattr(dep, "sourceline", 0),
                "body_text": f"{dg_t}:{da_t}:{dv_t}",
                "calls": [],
                "summary": f"[XML/Maven] dependency: {dg_t}:{da_t}:{dv_t}",
                "comment": "",
            })
    return units


def _parse_web_xml(root: etree._Element) -> list[dict[str, Any]]:
    units = []
    ns_map = root.nsmap or {}
    ns_prefix = next((f"{{{v}}}" for k, v in ns_map.items() if "javaee" in v or "web-app" in v), "")

    def _ftag(tag: str) -> str:
        return f"{ns_prefix}{tag}" if ns_prefix else tag

    for servlet in root.iter(_ftag("servlet")):
        name_el = servlet.find(_ftag("servlet-name"))
        class_el = servlet.find(_ftag("servlet-class"))
        name = name_el.text.strip() if name_el is not None and name_el.text else "unknown"
        cls = class_el.text.strip() if class_el is not None and class_el.text else ""
        units.append({
            "language": "xml",
            "unit_type": "web_servlet",
            "qualified_name": f"servlet.{name}",
            "name": name,
            "signature": f"<servlet-name>{name}</servlet-name>",
            "start_line": getattr(servlet, "sourceline", 0),
            "end_line": getattr(servlet, "sourceline", 0),
            "body_text": cls,
            "calls": [],
            "summary": f"[XML/web.xml] servlet: {name} | class: {cls}",
            "comment": "",
        })
    for flt in root.iter(_ftag("filter")):
        name_el = flt.find(_ftag("filter-name"))
        class_el = flt.find(_ftag("filter-class"))
        name = name_el.text.strip() if name_el is not None and name_el.text else "unknown"
        cls = class_el.text.strip() if class_el is not None and class_el.text else ""
        units.append({
            "language": "xml",
            "unit_type": "web_filter",
            "qualified_name": f"filter.{name}",
            "name": name,
            "signature": f"<filter-name>{name}</filter-name>",
            "start_line": getattr(flt, "sourceline", 0),
            "end_line": getattr(flt, "sourceline", 0),
            "body_text": cls,
            "calls": [],
            "summary": f"[XML/web.xml] filter: {name} | class: {cls}",
            "comment": "",
        })
    return units


def _parse_generic(root: etree._Element) -> list[dict[str, Any]]:
    units = []
    for i, elem in enumerate(root.iter()):
        if not isinstance(elem.tag, str):
            continue
        if len(elem) == 0 and elem.text and elem.text.strip():
            tag = etree.QName(elem.tag).localname
            val = elem.text.strip()[:200]
            units.append({
                "language": "xml",
                "unit_type": "xml_element",
                "qualified_name": f"{tag}_{i}",
                "name": tag,
                "signature": f"<{tag}>",
                "start_line": getattr(elem, "sourceline", 0),
                "end_line": getattr(elem, "sourceline", 0),
                "body_text": val,
                "calls": [],
                "summary": f"[XML] <{tag}>: {val[:80]}",
                "comment": "",
            })
    return units[:100]


def parse_xml(file_path: str) -> list[dict[str, Any]]:
    try:
        parser = etree.XMLParser(recover=True)
        tree = etree.parse(file_path, parser)
        root = tree.getroot()
        if root is None:
            return []
    except Exception:
        return []

    xml_type = _detect_xml_type(root)
    if xml_type == "mybatis_mapper":
        return _parse_mybatis(root, "")
    if xml_type == "spring_beans":
        return _parse_spring_beans(root)
    if xml_type == "maven_pom":
        return _parse_maven_pom(root)
    if xml_type == "web_xml":
        return _parse_web_xml(root)
    return _parse_generic(root)
