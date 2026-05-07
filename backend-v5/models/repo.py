from __future__ import annotations
from pydantic import BaseModel


class RepoCreate(BaseModel):
    name: str
    path: str


class RepoStats(BaseModel):
    java_files: int = 0
    jsp_files: int = 0
    javascript_files: int = 0
    xml_files: int = 0
    total_units: int = 0
    last_scanned_at: str | None = None
