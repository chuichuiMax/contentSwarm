from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass
class ContentApplicationError(Exception):
    code: str
    message: str
    kind: Literal["not_found", "conflict", "invalid"]

    def __str__(self) -> str:
        return self.message
