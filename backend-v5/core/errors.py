from __future__ import annotations
from fastapi import HTTPException


def not_found(detail: str = "Not found") -> HTTPException:
    return HTTPException(status_code=404, detail=detail)


def bad_request(detail: str) -> HTTPException:
    return HTTPException(status_code=400, detail=detail)


def repo_not_found() -> HTTPException:
    return not_found("Repo not found")
