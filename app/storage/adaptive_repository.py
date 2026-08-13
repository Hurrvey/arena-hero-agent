"""Fixed-window adaptive audit persistence."""

from __future__ import annotations

import hashlib
import json
import math
from uuid import uuid4

from .database import Database, utc_now
from .models import AdaptiveWindow


class AdaptiveRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def close_window(
        self,
        *,
        start_tick: int,
        end_tick: int,
        sample_count: int,
        base_revision: int,
        skill_fingerprint: str,
        raw_score: float,
        status: str,
    ) -> AdaptiveWindow:
        if end_tick <= start_tick or sample_count < 0 or not math.isfinite(raw_score):
            raise ValueError("adaptive window bounds or score are invalid")
        normalized = raw_score / max(1, sample_count)
        cycle_id = uuid4().hex
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO adaptive_cycles(
                    cycle_id, start_tick, end_tick, sample_count, base_revision,
                    candidate_revision, skill_fingerprint, raw_score,
                    normalized_score, status, created_at
                ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?)
                """,
                (
                    cycle_id,
                    start_tick,
                    end_tick,
                    sample_count,
                    base_revision,
                    skill_fingerprint,
                    raw_score,
                    normalized,
                    status,
                    utc_now(),
                ),
            )
            connection.commit()
        return AdaptiveWindow(
            cycle_id,
            start_tick,
            end_tick,
            sample_count,
            base_revision,
            None,
            skill_fingerprint,
            raw_score,
            normalized,
            status,
        )

    def windows(self, *, limit: int = 100) -> list[AdaptiveWindow]:
        if not 1 <= limit <= 500:
            raise ValueError("adaptive window limit is invalid")
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT cycle_id, start_tick, end_tick, sample_count,
                       base_revision, candidate_revision, skill_fingerprint,
                       raw_score, normalized_score, status
                FROM adaptive_cycles ORDER BY end_tick DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            AdaptiveWindow(
                cycle_id=str(row[0]),
                start_tick=int(row[1]),
                end_tick=int(row[2]),
                sample_count=int(row[3]),
                base_revision=int(row[4]),
                candidate_revision=int(row[5]) if row[5] is not None else None,
                skill_fingerprint=str(row[6]),
                raw_score=float(row[7]),
                normalized_score=float(row[8]),
                status=str(row[9]),
            )
            for row in rows
        ]

    def observe(self, *, tick: int, base_revision: int, projection: dict[str, object]) -> None:
        if tick < 0:
            raise ValueError("adaptive observation tick is invalid")
        encoded = json.dumps(projection, sort_keys=True, separators=(",", ":"))
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO adaptive_observations(
                    tick, base_revision, projection_json, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (tick, base_revision, encoded, utc_now()),
            )
            connection.commit()

    def observations(self, *, start_tick: int, end_tick: int) -> list[dict[str, object]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT projection_json FROM adaptive_observations
                WHERE tick > ? AND tick <= ? ORDER BY tick
                """,
                (start_tick, end_tick),
            ).fetchall()
        return [json.loads(str(row[0])) for row in rows]

    def last_observed_tick(self) -> int:
        with self.database.connect() as connection:
            row = connection.execute("SELECT MAX(tick) FROM adaptive_observations").fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    def last_sealed_tick(self) -> int:
        with self.database.connect() as connection:
            row = connection.execute("SELECT MAX(end_tick) FROM adaptive_cycles").fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    def create_candidate(
        self,
        *,
        cycle_id: str,
        base_revision: int,
        profile: dict[str, object],
        evaluator_report: dict[str, object],
        designer_report: dict[str, object],
        status: str = "REVIEW_REQUIRED",
    ) -> str:
        candidate_id = uuid4().hex
        profile_json = json.dumps(profile, sort_keys=True, separators=(",", ":"))
        report = {"evaluation": evaluator_report, "designer": designer_report}
        report_json = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        response_hash = hashlib.sha256(report_json.encode("utf-8")).hexdigest()
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO adaptive_candidates(
                    candidate_id, cycle_id, base_revision, profile_json,
                    evaluator_report_json, response_hash, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate_id,
                    cycle_id,
                    base_revision,
                    profile_json,
                    report_json,
                    response_hash,
                    status,
                    utc_now(),
                ),
            )
            connection.commit()
        return candidate_id

    def candidates(self, *, limit: int = 100) -> list[dict[str, object]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT c.candidate_id, c.cycle_id, c.base_revision, c.profile_json,
                       c.evaluator_report_json, c.response_hash, c.status, c.created_at,
                       w.candidate_revision,
                       w.skill_fingerprint, w.sample_count, w.start_tick, w.end_tick,
                       w.raw_score, w.normalized_score
                FROM adaptive_candidates AS c
                JOIN adaptive_cycles AS w ON w.cycle_id = c.cycle_id
                ORDER BY c.created_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "candidateId": str(row[0]),
                "cycleId": str(row[1]),
                "baseRevision": int(row[2]),
                "profile": json.loads(str(row[3])),
                "report": json.loads(str(row[4])),
                "responseHash": str(row[5]),
                "status": str(row[6]),
                "createdAt": str(row[7]),
                "candidateRevision": int(row[8]) if row[8] is not None else None,
                "skillFingerprint": str(row[9]),
                "sampleCount": int(row[10]),
                "startTick": int(row[11]),
                "endTick": int(row[12]),
                "rawScore": float(row[13]),
                "scorePerTick": float(row[14]),
            }
            for row in rows
        ]

    def candidate(self, candidate_id: str) -> dict[str, object]:
        match = next(
            (
                candidate
                for candidate in self.candidates(limit=500)
                if candidate["candidateId"] == candidate_id
            ),
            None,
        )
        if match is None:
            raise LookupError("adaptive candidate was not found")
        return match

    def mark_candidate(
        self,
        candidate_id: str,
        *,
        status: str,
        candidate_revision: int | None = None,
    ) -> None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT cycle_id FROM adaptive_candidates WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
            if row is None:
                raise LookupError("adaptive candidate was not found")
            connection.execute(
                "UPDATE adaptive_candidates SET status = ? WHERE candidate_id = ?",
                (status, candidate_id),
            )
            connection.execute(
                """
                UPDATE adaptive_cycles SET status = ?, candidate_revision = COALESCE(?, candidate_revision)
                WHERE cycle_id = ?
                """,
                (status, candidate_revision, row[0]),
            )
            connection.commit()

    def mark_candidate_if_reviewable(self, candidate_id: str, *, status: str) -> bool:
        """Update lifecycle only while no apply transaction has committed."""

        with self.database.connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(
                    """
                    UPDATE adaptive_candidates
                    SET status = ?
                    WHERE candidate_id = ? AND status IN ('READY', 'REVIEW_REQUIRED', 'STALE')
                      AND EXISTS (
                          SELECT 1 FROM adaptive_cycles AS w
                          WHERE w.cycle_id = adaptive_candidates.cycle_id
                            AND w.candidate_revision IS NULL
                      )
                    """,
                    (status, candidate_id),
                )
                if cursor.rowcount != 1:
                    connection.rollback()
                    return False
                connection.execute(
                    """
                    UPDATE adaptive_cycles
                    SET status = ?
                    WHERE cycle_id = (
                        SELECT cycle_id FROM adaptive_candidates WHERE candidate_id = ?
                    ) AND candidate_revision IS NULL
                    """,
                    (status, candidate_id),
                )
                connection.commit()
                return True
            except Exception:
                connection.rollback()
                raise

    def reject_candidate(self, candidate_id: str) -> bool:
        """Reject only a candidate that has not created a strategy revision."""

        allowed = ("READY", "REVIEW_REQUIRED", "STALE")
        with self.database.connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    """
                    SELECT c.cycle_id, c.status, w.candidate_revision
                    FROM adaptive_candidates AS c
                    JOIN adaptive_cycles AS w ON w.cycle_id = c.cycle_id
                    WHERE c.candidate_id = ?
                    """,
                    (candidate_id,),
                ).fetchone()
                if row is None:
                    raise LookupError("adaptive candidate was not found")
                if str(row[1]) not in allowed or row[2] is not None:
                    connection.rollback()
                    return False
                connection.execute(
                    "UPDATE adaptive_candidates SET status = 'REJECTED' WHERE candidate_id = ?",
                    (candidate_id,),
                )
                connection.execute(
                    "UPDATE adaptive_cycles SET status = 'REJECTED' WHERE cycle_id = ?",
                    (row[0],),
                )
                connection.commit()
                return True
            except Exception:
                connection.rollback()
                raise

    def apply_candidate_revision(
        self,
        candidate_id: str,
        *,
        expected_revision: int,
        profile: dict[str, object],
    ) -> tuple[int | None, str]:
        """Atomically bind a candidate to its immutable pending strategy revision."""

        with self.database.connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                candidate = connection.execute(
                    """
                    SELECT c.status, c.cycle_id, w.candidate_revision
                    FROM adaptive_candidates AS c
                    JOIN adaptive_cycles AS w ON w.cycle_id = c.cycle_id
                    WHERE c.candidate_id = ?
                    """,
                    (candidate_id,),
                ).fetchone()
                if candidate is None:
                    raise LookupError("adaptive candidate was not found")
                status = str(candidate[0])
                if status == "PENDING_ACTIVATION" and candidate[2] is not None:
                    connection.rollback()
                    return int(candidate[2]), "PENDING_ACTIVATION"
                if status not in {"READY", "REVIEW_REQUIRED"}:
                    connection.rollback()
                    return None, f"CANDIDATE_STATE_{status}"
                active = connection.execute(
                    "SELECT revision FROM strategy_profiles WHERE status = 'ACTIVE'"
                ).fetchone()
                pending = connection.execute(
                    "SELECT revision FROM strategy_profiles WHERE status = 'PENDING'"
                ).fetchone()
                if active is None or int(active[0]) != expected_revision or pending is not None:
                    connection.execute(
                        "UPDATE adaptive_candidates SET status = 'STALE' WHERE candidate_id = ?",
                        (candidate_id,),
                    )
                    connection.execute(
                        "UPDATE adaptive_cycles SET status = 'STALE' WHERE cycle_id = ?",
                        (candidate[1],),
                    )
                    connection.commit()
                    return None, "STRATEGY_REVISION_CHANGED"
                cursor = connection.execute(
                    """
                    INSERT INTO strategy_profiles(
                        source, parent_revision, profile_json, reason,
                        activated_tick, status, created_at
                    ) VALUES ('ADAPTIVE', ?, ?, ?, NULL, 'PENDING', ?)
                    """,
                    (
                        expected_revision,
                        json.dumps(profile, sort_keys=True, separators=(",", ":")),
                        f"adaptive candidate {candidate_id}",
                        utc_now(),
                    ),
                )
                revision = int(cursor.lastrowid)
                connection.execute(
                    "UPDATE adaptive_candidates SET status = 'PENDING_ACTIVATION' WHERE candidate_id = ?",
                    (candidate_id,),
                )
                connection.execute(
                    """
                    UPDATE adaptive_cycles
                    SET status = 'PENDING_ACTIVATION', candidate_revision = ?
                    WHERE cycle_id = ?
                    """,
                    (revision, candidate[1]),
                )
                connection.commit()
                return revision, "PENDING_ACTIVATION"
            except Exception:
                connection.rollback()
                raise
