"""Boundary for article-local draft checks and explicit continuation."""
from typing import Protocol


class DraftPort(Protocol):
    def get(self, article_id: str) -> dict: ...
    def command(self, article_id: str, action: str, values: dict) -> dict: ...
