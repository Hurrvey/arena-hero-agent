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

    def series(
        self,
        session_id: str | None = None,
        *,
        limit: int = 1000,
    ) -> list[dict[str, object]]:
        if not 1 <= limit <= 1000:
            raise ValueError("metric series limit is invalid")
        parameters: tuple[object, ...]
        where = ""
        if session_id is None:
            parameters = (limit,)
        else:
            where = "WHERE session_id = ?"
            parameters = (session_id, limit)
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT session_id, tick, metric_name, metric_value
                FROM metric_points {where}
                ORDER BY tick DESC, metric_name
                LIMIT ?
                """,
                parameters,
            ).fetchall()
        points: dict[tuple[str, int], dict[str, object]] = {}
        for row in reversed(rows):
            key = (str(row[0]), int(row[1]))
            point = points.setdefault(key, {"sessionId": key[0], "tick": key[1]})
            point[str(row[2])] = float(row[3])
        return sorted(points.values(), key=lambda item: int(item["tick"]))

    def summary(self, session_id: str | None = None) -> dict[str, object]:
        points = self.series(session_id)
        if not points:
            return {"ticks": 0, "resources": 0, "population": 0, "beaconTicks": 0}
        latest = points[-1]
        return {
            "ticks": len(points),
            "lastTick": latest["tick"],
            "resources": latest.get("resources", 0),
            "population": latest.get("population", 0),
            "beaconTicks": sum(float(point.get("beaconOwned", 0)) for point in points),
        }
