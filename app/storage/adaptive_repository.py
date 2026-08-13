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
                "skillFingerprint": str(row[8]),
                "sampleCount": int(row[9]),
                "startTick": int(row[10]),
                "endTick": int(row[11]),
                "rawScore": float(row[12]),
                "scorePerTick": float(row[13]),
            }
            for row in rows
        ]

    def candidate(self, candidate_id: str) -> dict[str, object]:
        match = next(
            (candidate for candidate in self.candidates(limit=500) if candidate["candidateId"] == candidate_id),
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
