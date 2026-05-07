import logging
from .java_parser import parse_java
from .jsp_parser import parse_jsp
from .js_parser import parse_js
from .xml_parser import parse_xml
from typing import Any

logger = logging.getLogger("parsers")

_DISPATCH = {
    "java": parse_java,
    "jsp": parse_jsp,
    "javascript": parse_js,
    "xml": parse_xml,
}


def parse_file(file_path: str, language: str) -> list[dict[str, Any]]:
    fn = _DISPATCH.get(language)
    if fn is None:
        return []
    try:
        return fn(file_path)
    except Exception as e:
        logger.warning("parse_file failed: %s [%s] %s", file_path, language, e)
        return []
