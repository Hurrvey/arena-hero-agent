"""Local-only Web service settings without import-time secret loading."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Self

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True, slots=True)
class Settings:
    database_path: Path = PROJECT_ROOT / "data" / "arena_hero_agent.db"
    lock_directory: Path = PROJECT_ROOT / "data" / "locks"
    static_directory: Path = PROJECT_ROOT / "frontend"
    asset_directory: Path = PROJECT_ROOT / "arena-hero-ui-assets"
    dotenv_path: Path = PROJECT_ROOT / ".env"
    legacy_adaptive_directory: Path = PROJECT_ROOT / "adaptive"
    websocket_replay_limit: int = 1000
    websocket_client_queue: int = 256
    host: str = "127.0.0.1"
    port: int = 8765

    def __post_init__(self) -> None:
        for name in (
            "database_path",
            "lock_directory",
            "static_directory",
            "asset_directory",
            "dotenv_path",
            "legacy_adaptive_directory",
        ):
            object.__setattr__(self, name, Path(getattr(self, name)).resolve())
        if not 1 <= self.websocket_replay_limit <= 1000:
            raise ValueError("websocket_replay_limit must be between 1 and 1000")
        if not 1 <= self.websocket_client_queue <= 4096:
            raise ValueError("websocket_client_queue must be between 1 and 4096")
        if self.host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("the MVP Web service must bind to loopback")
        if not 1 <= self.port <= 65535:
            raise ValueError("port is invalid")

    def with_updates(self, **changes: object) -> Self:
        return replace(self, **changes)
