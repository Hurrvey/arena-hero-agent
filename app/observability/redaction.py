"""Deterministic public short IDs and secret-field removal."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_SECRET_KEYS = {
    "api_key",
    "authorization",
    "owner_username",
    "prompt",
    "response",
    "raw_payload",
}


@dataclass(slots=True)
class PublicIdMapper:
    session_id: str
    _ids: dict[str, str] = field(default_factory=dict)

    def short(self, identifier: object) -> str:
        key = str(identifier)
        if key not in self._ids:
            self._ids[key] = f"E{len(self._ids) + 1}"
        return self._ids[key]


def _camel(name: str) -> str:
    head, *tail = name.split("_")
    return head + "".join(piece[:1].upper() + piece[1:] for piece in tail)


def redact_public_payload(value: Any, mapper: PublicIdMapper) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key).lower()
            if name in _SECRET_KEYS or "token" in name or "secret" in name:
                continue
            public_key = _camel(str(key))
            if name == "id" or name.endswith("_id"):
                result[public_key] = mapper.short(item)
            else:
                result[public_key] = redact_public_payload(item, mapper)
        return result
    if isinstance(value, (tuple, list)):
        return [redact_public_payload(item, mapper) for item in value]
    if isinstance(value, str) and _UUID.fullmatch(value):
        return mapper.short(value)
    return value
