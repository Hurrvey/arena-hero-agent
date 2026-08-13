"""Read-only, idempotent import of the previous file-based adaptive state."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from app.storage.database import Database, utc_now
from app.storage.strategy_repository import RevisionConflict, StrategyRepository
from strategy_policy import StrategyProfile

_MAX_STATE_BYTES = 1_000_000


@dataclass(frozen=True, slots=True)
class LegacyImportResult:
    imported: int = 0
    skipped: int = 0
    warnings: tuple[str, ...] = ()


class LegacyImporter:
    def __init__(
        self,
        database: Database,
        strategies: StrategyRepository,
        legacy_directory: str | Path,
    ) -> None:
        self.database = database
        self.strategies = strategies
        self.legacy_directory = Path(legacy_directory).resolve()

    def run(self) -> LegacyImportResult:
        state_path = self.legacy_directory / "state.json"
        if not state_path.is_file():
            return LegacyImportResult()
        try:
            content = state_path.read_bytes()
            if len(content) > _MAX_STATE_BYTES:
                raise ValueError
            digest = hashlib.sha256(content).hexdigest()
            source = str(state_path)
            if self._already_imported(source, digest):
                return LegacyImportResult(skipped=1)
            payload = json.loads(content.decode("utf-8"))
            if not isinstance(payload, dict):
                raise TypeError
            raw_profile = payload.get("active_profile", payload.get("profile"))
            profile = StrategyProfile.from_mapping(raw_profile)
        except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
            return LegacyImportResult(warnings=("legacy state ignored: invalid or oversized",))

        active = self.strategies.current()
        try:
            self.strategies.create_revision(
                expected_revision=active.revision,
                profile=profile,
                source="LEGACY_IMPORT",
                reason="imported from previous adaptive state",
            )
        except RevisionConflict:
            return LegacyImportResult(warnings=("legacy state ignored: pending revision exists",))
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO legacy_imports(
                    source_path, content_hash, status, detail, imported_at
                ) VALUES (?, ?, 'IMPORTED', 'strategy profile', ?)
                """,
                (source, digest, utc_now()),
            )
            connection.commit()
        return LegacyImportResult(imported=1)

    def _already_imported(self, source: str, digest: str) -> bool:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM legacy_imports
                WHERE source_path = ? AND content_hash = ? AND status = 'IMPORTED'
                """,
                (source, digest),
            ).fetchone()
        return row is not None
