"""Fixed-window adaptive audit persistence."""

from __future__ import annotations

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
