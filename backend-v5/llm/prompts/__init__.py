from __future__ import annotations
from config import SUPPORTED_LANGUAGES

from . import zh, en, ja

_MODULES = {"zh": zh, "en": en, "ja": ja}


def get_search_prompt(query: str, hits: list[dict], lang: str = "zh") -> list[dict]:
    mod = _MODULES.get(lang if lang in SUPPORTED_LANGUAGES else "zh")
    return mod.search_prompt(query, hits)


def get_impact_prompt(impact_chains: list[dict], lang: str = "zh") -> list[dict]:
    mod = _MODULES.get(lang if lang in SUPPORTED_LANGUAGES else "zh")
    return mod.impact_prompt(impact_chains)
