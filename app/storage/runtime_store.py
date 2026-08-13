"""Atomic snapshots, plans, resolution results, and service event queries."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from uuid import uuid4

from .database import Database, utc_now
from .models import EventPage, RuntimeSession, ServiceEvent


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class RuntimeStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create_session(self, *, account_hash: str) -> RuntimeSession:
        session = RuntimeSession(uuid4().hex, account_hash, "STOPPED", utc_now())
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO runtime_sessions(session_id, account_hash, status, started_at)
                VALUES (?, ?, ?, ?)
                """,
                (session.session_id, session.account_hash, session.status, session.started_at),
            )
            connection.commit()
        return session

    def update_status(
        self,
        session_id: str,
        status: str,
        *,
        last_tick: int | None = None,
        error_code: str | None = None,
        ended: bool = False,
    ) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE runtime_sessions
                SET status = ?, last_tick = COALESCE(?, last_tick), error_code = ?,
                    ended_at = CASE WHEN ? THEN ? ELSE ended_at END
                WHERE session_id = ?
                """,
                (status, last_tick, error_code, ended, utc_now(), session_id),
            )
            connection.commit()

    def append_service_event(
        self,
        *,
        session_id: str,
        tick: int | None,
        event_type: str,
        payload: Mapping[str, object],
    ) -> ServiceEvent:
        created_at = utc_now()
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO service_events(
                    session_id, tick, event_type, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (session_id, tick, event_type, _json(payload), created_at),
            )
            connection.commit()
        return ServiceEvent(
            int(cursor.lastrowid),
            session_id,
            tick,
            event_type,
            dict(payload),
            created_at,
        )

    def save_turn_batch(
        self,
        *,
        session_id: str,
        tick: int,
        raw_snapshot: Mapping[str, object],
        public_snapshot: Mapping[str, object],
        raw_plan: Mapping[str, object],
        public_plan: Mapping[str, object],
        explanation: Mapping[str, object],
        resolution_events: Sequence[Mapping[str, object]],
        service_events: Sequence[tuple[str, Mapping[str, object]]],
        strategy_revision: int | None = None,
        plan_status: str = "DRAFT",
    ) -> tuple[ServiceEvent, ...]:
        created_at = utc_now()
        committed: list[ServiceEvent] = []
        with self.database.connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT INTO turn_snapshots(
                        session_id, tick, received_at, raw_payload_json, public_payload_json
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (session_id, tick, created_at, _json(raw_snapshot), _json(public_snapshot)),
                )
                connection.execute(
                    """
                    INSERT INTO plans(
                        session_id, tick, strategy_revision, status, raw_plan_json,
                        public_plan_json, explanation_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        tick,
                        strategy_revision,
                        plan_status,
                        _json(raw_plan),
                        _json(public_plan),
                        _json(explanation),
                        created_at,
                    ),
                )
                for event in resolution_events:
                    connection.execute(
                        """
                        INSERT INTO resolution_events(
                            session_id, plan_tick, observed_tick, event_type, short_id,
                            public_payload_json, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            session_id,
                            int(event.get("plan_tick", max(0, tick - 1))),
                            tick,
                            str(event["event_type"]),
                            event.get("short_id"),
                            _json(event),
                            created_at,
                        ),
                    )
                for event_type, payload in service_events:
                    cursor = connection.execute(
                        """
                        INSERT INTO service_events(
                            session_id, tick, event_type, payload_json, created_at
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (session_id, tick, event_type, _json(payload), created_at),
                    )
                    committed.append(
                        ServiceEvent(
                            int(cursor.lastrowid),
                            session_id,
                            tick,
                            event_type,
                            dict(payload),
                            created_at,
                        )
                    )
                connection.execute(
                    "UPDATE runtime_sessions SET last_tick = ? WHERE session_id = ?",
                    (tick, session_id),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return tuple(committed)

    def events_after(self, after_seq: int, *, limit: int = 200) -> EventPage:
        if after_seq < 0 or not 1 <= limit <= 1000:
            raise ValueError("event page bounds are invalid")
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT seq, session_id, tick, event_type, payload_json, created_at
                FROM service_events WHERE seq > ? ORDER BY seq LIMIT ?
                """,
                (after_seq, limit),
            ).fetchall()
        events = tuple(
            ServiceEvent(row[0], row[1], row[2], row[3], json.loads(row[4]), row[5]) for row in rows
        )
        return EventPage(events, events[-1].seq if events else after_seq)

    def current_state(self, session_id: str) -> dict[str, object] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT public_payload_json FROM turn_snapshots
                WHERE session_id = ? ORDER BY tick DESC LIMIT 1
                """,
                (session_id,),
            ).fetchone()
        return json.loads(row[0]) if row is not None else None

    def state_at(self, session_id: str, tick: int) -> dict[str, object] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT public_payload_json FROM turn_snapshots
                WHERE session_id = ? AND tick = ?
                """,
                (session_id, tick),
            ).fetchone()
        return json.loads(row[0]) if row is not None else None

    def current_plan(self, session_id: str) -> dict[str, object] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT public_plan_json, explanation_json, status, tick
                FROM plans WHERE session_id = ? ORDER BY tick DESC LIMIT 1
                """,
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "plan": json.loads(row[0]),
            "explanation": json.loads(row[1]),
            "status": row[2],
            "tick": row[3],
        }

    def plan_at(self, session_id: str, tick: int) -> dict[str, object] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT public_plan_json, explanation_json, status, receipt_json
                FROM plans WHERE session_id = ? AND tick = ?
                """,
                (session_id, tick),
            ).fetchone()
        if row is None:
            return None
        return {
            "plan": json.loads(row[0]),
            "explanation": json.loads(row[1]),
            "status": str(row[2]),
            "receipt": json.loads(row[3]) if row[3] else None,
            "tick": tick,
        }

    def event_markers(
        self,
        session_id: str | None = None,
        *,
        limit: int = 300,
    ) -> list[dict[str, object]]:
        if not 1 <= limit <= 1000:
            raise ValueError("event marker limit is invalid")
        where = "WHERE tick IS NOT NULL"
        parameters: tuple[object, ...]
        if session_id is None:
            parameters = (limit,)
        else:
            where += " AND session_id = ?"
            parameters = (session_id, limit)
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT tick, event_type, created_at FROM service_events
                {where} ORDER BY seq DESC LIMIT ?
                """,
                parameters,
            ).fetchall()
        return [
            {"tick": int(row[0]), "eventType": str(row[1]), "createdAt": str(row[2])}
            for row in reversed(rows)
        ]
