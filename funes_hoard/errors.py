"""Input errors that surface to callers as `{"error": code, "message": ...}` 400s.

Raised from the core/query layer (no FastAPI imports here); `api.py` turns
them into JSON responses. Messages are written for a small local model:
they say what was wrong *and* what to pass instead.
"""
from __future__ import annotations


class BadInput(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
