"""Compare-and-swap immutable strategy profile revisions."""

from __future__ import annotations

import json

from strategy_policy import StrategyProfile

from .database import Database, utc_now
from .models import StrategyRevision


class RevisionConflict(RuntimeError):
    pass


def _record(row: tuple[object, ...]) -> StrategyRevision:
    return StrategyRevision(
        revision=int(row[0]),
        source=str(row[1]),
        parent_revision=int(row[2]) if row[2] is not None else None,
        profile=StrategyProfile.from_mapping(json.loads(str(row[3]))),
        reason=str(row[4]),
        activated_tick=int(row[5]) if row[5] is not None else None,
        status=str(row[6]),
        created_at=str(row[7]),
    )


_SELECT = """
SELECT revision, source, parent_revision, profile_json, reason,
       activated_tick, status, created_at
FROM strategy_profiles
"""


class StrategyRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def ensure_initial(self, profile: StrategyProfile) -> StrategyRevision:
        profile.validate()
        with self.database.connect() as connection:
            row = connection.execute(f"{_SELECT} WHERE status = 'ACTIVE'").fetchone()
            if row is None:
                now = utc_now()
                cursor = connection.execute(
                    """
                    INSERT INTO strategy_profiles(
                        source, parent_revision, profile_json, reason,
                        activated_tick, status, created_at
                    ) VALUES ('DEFAULT', NULL, ?, 'initial default', 0, 'ACTIVE', ?)
                    """,
                    (_profile_json(profile), now),
                )
                connection.commit()
                row = connection.execute(
                    f"{_SELECT} WHERE revision = ?", (cursor.lastrowid,)
                ).fetchone()
        return _record(row)

    def current(self) -> StrategyRevision:
        with self.database.connect() as connection:
            row = connection.execute(f"{_SELECT} WHERE status = 'ACTIVE'").fetchone()
        if row is None:
            raise LookupError("no active strategy revision")
        return _record(row)

    def pending(self) -> StrategyRevision | None:
        with self.database.connect() as connection:
            row = connection.execute(f"{_SELECT} WHERE status = 'PENDING'").fetchone()
        return _record(row) if row is not None else None

    def get(self, revision: int) -> StrategyRevision:
        with self.database.connect() as connection:
            row = connection.execute(f"{_SELECT} WHERE revision = ?", (revision,)).fetchone()
        if row is None:
            raise LookupError(f"unknown strategy revision: {revision}")
        return _record(row)

    def create_revision(
        self,
        *,
        expected_revision: int,
        profile: StrategyProfile,
        source: str,
        reason: str,
    ) -> StrategyRevision:
        profile.validate()
        with self.database.connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                active = connection.execute(
                    "SELECT revision FROM strategy_profiles WHERE status = 'ACTIVE'"
                ).fetchone()
                pending = connection.execute(
                    "SELECT revision FROM strategy_profiles WHERE status = 'PENDING'"
                ).fetchone()
                if active is None or int(active[0]) != expected_revision or pending is not None:
                    raise RevisionConflict("strategy revision changed")
                cursor = connection.execute(
                    """
                    INSERT INTO strategy_profiles(
                        source, parent_revision, profile_json, reason,
                        activated_tick, status, created_at
                    ) VALUES (?, ?, ?, ?, NULL, 'PENDING', ?)
                    """,
                    (source, expected_revision, _profile_json(profile), reason, utc_now()),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self.get(int(cursor.lastrowid))

    def activate_pending(self, *, tick: int) -> StrategyRevision | None:
        with self.database.connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                pending = connection.execute(
                    "SELECT revision FROM strategy_profiles WHERE status = 'PENDING'"
                ).fetchone()
                if pending is None:
                    connection.rollback()
                    return None
                active = connection.execute(
                    "SELECT revision FROM strategy_profiles WHERE status = 'ACTIVE'"
                ).fetchone()
                if active is not None:
                    connection.execute(
                        "UPDATE strategy_profiles SET status = 'SUPERSEDED' WHERE revision = ?",
                        (active[0],),
                    )
                connection.execute(
                    """
                    UPDATE strategy_profiles
                    SET status = 'ACTIVE', activated_tick = ? WHERE revision = ?
                    """,
                    (tick, pending[0]),
                )
                connection.commit()
                revision = int(pending[0])
            except Exception:
                connection.rollback()
                raise
        return self.get(revision)

    def rollback(
        self,
        *,
        expected_revision: int,
        target_revision: int,
        reason: str,
    ) -> StrategyRevision:
        target = self.get(target_revision)
        return self.create_revision(
            expected_revision=expected_revision,
            profile=target.profile,
            source="ROLLBACK",
            reason=reason,
        )


def _profile_json(profile: StrategyProfile) -> str:
    return json.dumps(profile.to_mapping(), sort_keys=True, separators=(",", ":"))
