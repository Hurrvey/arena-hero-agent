"""Stable public error envelope and internal domain exception."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class AppError(Exception):
    code: str
    message: str
    status_code: int
    details: dict[str, object] = field(default_factory=dict)
