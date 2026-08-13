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
                UPDATE turn_snapshots SET raw_payload_json = '{}'
                WHERE rowid IN (
                    SELECT rowid FROM turn_snapshots
                    WHERE received_at < ? AND raw_payload_json != '{}' LIMIT ?
                )
                """,
                (raw_cutoff, batch),
            ).rowcount
            raw_plans = connection.execute(
                """
                UPDATE plans SET raw_plan_json = '{}'
                WHERE rowid IN (
                    SELECT plans.rowid FROM plans
                    JOIN turn_snapshots USING (session_id, tick)
                    WHERE turn_snapshots.received_at < ? AND plans.raw_plan_json != '{}'
                    LIMIT ?
                )
                """,
                (raw_cutoff, batch),
            ).rowcount
            raw_receipts = connection.execute(
                """
                UPDATE plan_receipts SET raw_plan_json = '{}'
                WHERE rowid IN (
                    SELECT rowid FROM plan_receipts
                    WHERE received_at < ? AND raw_plan_json != '{}' LIMIT ?
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
            resolutions = connection.execute(
                """
                DELETE FROM resolution_events WHERE id IN (
                    SELECT id FROM resolution_events WHERE created_at < ? LIMIT ?
                )
                """,
                (event_cutoff, batch),
            ).rowcount
            connection.commit()
        return raw + raw_plans + raw_receipts + events + resolutions

    def prune_all(self, *, raw_days: int = 7, event_days: int = 30, batch: int = 500) -> int:
        """Drain expired rows through repeated short transactions at startup."""

        total = 0
        while True:
            deleted = self.prune(raw_days=raw_days, event_days=event_days, batch=batch)
            total += deleted
            if deleted == 0:
                return total
