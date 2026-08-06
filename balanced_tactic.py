from __future__ import annotations

import os
from getpass import getpass

from arena_hero import ArenaHeroClient, Direction, UnitType


def choose_actions(turn) -> None:
    return None


def load_api_key() -> str:
    return os.environ.get("ARENA_HERO_API_KEY") or getpass("Arena Hero API key: ")


def play(api_key: str | None = None) -> None:
    raise NotImplementedError
