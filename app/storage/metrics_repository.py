"""Bounded metric point storage for history charts."""

from __future__ import annotations

import math

from .database import Database, utc_now


class MetricsRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def save(self, session_id: str, tick: int, values: dict[str, float]) -> None:
        if any(not math.isfinite(float(value)) for value in values.values()):
            raise ValueError("metric values must be finite")
        with self.database.connect() as connection:
            connection.executemany(
                """
                INSERT OR REPLACE INTO metric_points(
                    session_id, tick, metric_name, metric_value, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    (session_id, tick, name, float(value), utc_now())
                    for name, value in sorted(values.items())
                ),
            )
            connection.commit()
