"""Narrow official SDK client protocols and production factory."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol

from arena_hero import ArenaHeroClient


class GameClient(Protocol):
    def events(self) -> Iterator[object]: ...

    def close(self) -> None: ...


class GameClientFactory(Protocol):
    def __call__(self, api_key: str) -> GameClient: ...


def sdk_client_factory(api_key: str) -> GameClient:
    return ArenaHeroClient(api_key=api_key)
