"""Small off-command-path retention batches."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from .database import Database


class RetentionService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def prune(self, *, raw_days: int = 7, event_days: int = 30, batch: int = 500) -> int:
        if raw_days < 1 or event_days < 1 or not 1 <= batch <= 5000:
            raise ValueError("retention bounds are invalid")
        raw_cutoff = (datetime.now(UTC) - timedelta(days=raw_days)).isoformat()
        event_cutoff = (datetime.now(UTC) - timedelta(days=event_days)).isoformat()
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            raw = connection.execute(
                """
                DELETE FROM turn_snapshots WHERE rowid IN (
                    SELECT rowid FROM turn_snapshots WHERE received_at < ? LIMIT ?
                )
                """,
                (raw_cutoff, batch),
            ).rowcount
            events = connection.execute(
                """
                DELETE FROM service_events WHERE seq IN (
                    SELECT seq FROM service_events WHERE created_at < ? LIMIT ?
                )
                """,
                (event_cutoff, batch),
            ).rowcount
            connection.commit()
        return raw + events
