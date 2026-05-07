from __future__ import annotations
from pydantic import BaseModel
from config import DEFAULT_LANGUAGE


class SearchRequest(BaseModel):
    query: str
    n_results: int = 5
    language: str | None = None
    lang: str = DEFAULT_LANGUAGE
