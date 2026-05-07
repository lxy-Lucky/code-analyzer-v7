from __future__ import annotations
from pydantic import BaseModel
from config import DEFAULT_LANGUAGE


class AnalysisRequest(BaseModel):
    mode: str = "head"          # head | between | methods | recent
    base: str = "HEAD"
    compare: str | None = None
    qualified_names: list[str] | None = None
    lang: str = DEFAULT_LANGUAGE


class HookTrigger(BaseModel):
    repo_path: str
