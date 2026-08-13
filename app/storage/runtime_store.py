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

    def save_receipt(
        self,
        *,
        session_id: str,
        tick: int,
        receipt: Mapping[str, object],
        raw_plan: Mapping[str, object] | None = None,
        public_plan: Mapping[str, object] | None = None,
    ) -> ServiceEvent:
        """Attach a public receipt and publish it in one SQLite transaction."""

        created_at = utc_now()
        accepted = bool(receipt.get("accepted", True))
        status = "ACCEPTED" if accepted else "REJECTED"
        source = str(receipt.get("source", "UNKNOWN")).upper()
        if source not in {"AGENT", "MANUAL"}:
            raise ValueError("receipt source must be AGENT or MANUAL")
        with self.database.connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    UPDATE plans SET receipt_json = ?, status = ?
                    WHERE session_id = ? AND tick = ?
                    """,
                    (_json(receipt), status, session_id, tick),
                )
                connection.execute(
                    """
                    INSERT INTO plan_receipts(
                        session_id, tick, source, status, receipt_json,
                        raw_plan_json, public_plan_json, received_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(session_id, tick, source) DO UPDATE SET
                        status = excluded.status,
                        receipt_json = excluded.receipt_json,
                        raw_plan_json = excluded.raw_plan_json,
                        public_plan_json = excluded.public_plan_json,
                        received_at = excluded.received_at
                    """,
                    (
                        session_id,
                        tick,
                        source,
                        status,
                        _json(receipt),
                        _json(raw_plan or {}),
                        _json(public_plan or {}),
                        created_at,
                    ),
                )
                cursor = connection.execute(
                    """
                    INSERT INTO service_events(
                        session_id, tick, event_type, payload_json, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        tick,
                        "plan.accepted" if accepted else "plan.rejected",
                        _json({"source": source}),
                        created_at,
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return ServiceEvent(
            int(cursor.lastrowid),
            session_id,
            tick,
            "plan.accepted" if accepted else "plan.rejected",
            {"source": source},
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
        resolve_plan_tick: int | None = None,
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
                if resolve_plan_tick is not None:
                    connection.execute(
                        """
                        UPDATE plans SET status = 'RESOLVED'
                        WHERE session_id = ? AND tick = ?
                          AND status IN ('ACCEPTED', 'RECEIVED')
                        """,
                        (session_id, resolve_plan_tick),
                    )
                for event in resolution_events:
                    event_plan_tick = int(event.get("plan_tick", max(0, tick - 1)))
                    connection.execute(
                        """
                        UPDATE plans SET status = 'RESOLVED'
                        WHERE session_id = ? AND tick = ?
                          AND status IN ('ACCEPTED', 'RECEIVED')
                        """,
                        (session_id, event_plan_tick),
                    )
                    connection.execute(
                        """
                        INSERT INTO resolution_events(
                            session_id, plan_tick, observed_tick, event_type, short_id,
                            public_payload_json, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            session_id,
                            event_plan_tick,
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

    def latest_events(self, *, limit: int = 200) -> EventPage:
        """Return the newest bounded event window in ascending sequence order."""

        if not 1 <= limit <= 1000:
            raise ValueError("event page bounds are invalid")
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT seq, session_id, tick, event_type, payload_json, created_at
                FROM service_events ORDER BY seq DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        rows.reverse()
        events = tuple(
            ServiceEvent(row[0], row[1], row[2], row[3], json.loads(row[4]), row[5]) for row in rows
        )
        return EventPage(events, events[-1].seq if events else 0)

    def latest_event_seq(self) -> int:
        with self.database.connect() as connection:
            row = connection.execute("SELECT COALESCE(MAX(seq), 0) FROM service_events").fetchone()
        return int(row[0])

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
        receipts = self._receipts(session_id, int(row[3]))
        return {
            "plan": _effective_plan(json.loads(row[0]), receipts),
            "explanation": json.loads(row[1]),
            "status": row[2],
            "tick": row[3],
            "receipts": receipts,
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
            event_rows = connection.execute(
                """
                SELECT public_payload_json FROM resolution_events
                WHERE session_id = ? AND plan_tick = ? ORDER BY id
                """,
                (session_id, tick),
            ).fetchall()
        if row is None:
            return None
        receipts = self._receipts(session_id, tick)
        return {
            "plan": _effective_plan(json.loads(row[0]), receipts),
            "explanation": json.loads(row[1]),
            "status": str(row[2]),
            "receipt": json.loads(row[3]) if row[3] else None,
            "receipts": receipts,
            "resolutionEvents": [json.loads(item[0]) for item in event_rows],
            "tick": tick,
        }

    def _receipts(self, session_id: str, tick: int) -> dict[str, dict[str, object]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT source, status, receipt_json, public_plan_json, received_at
                FROM plan_receipts WHERE session_id = ? AND tick = ? ORDER BY source
                """,
                (session_id, tick),
            ).fetchall()
        return {
            str(row[0]): {
                "status": str(row[1]),
                "receipt": json.loads(row[2]),
                "plan": json.loads(row[3]),
                "receivedAt": str(row[4]),
            }
            for row in rows
        }

    def event_markers(
        self,
        session_id: str | None = None,
        *,
        limit: int = 300,
    ) -> list[dict[str, object]]:
        if not 1 <= limit <= 1000:
            raise ValueError("event marker limit is invalid")
        with self.database.connect() as connection:
            if session_id is None:
                rows = connection.execute(
                    """
                    SELECT tick, event_type, created_at FROM service_events
                    WHERE tick IS NOT NULL ORDER BY seq DESC LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT tick, event_type, created_at FROM service_events
                    WHERE tick IS NOT NULL AND session_id = ?
                    ORDER BY seq DESC LIMIT ?
                    """,
                    (session_id, limit),
                ).fetchall()
        return [
            {"tick": int(row[0]), "eventType": str(row[1]), "createdAt": str(row[2])}
            for row in reversed(rows)
        ]


def _effective_plan(
    planned: dict[str, object], receipts: Mapping[str, Mapping[str, object]]
) -> dict[str, object]:
    agent = receipts.get("AGENT", {}).get("plan")
    manual = receipts.get("MANUAL", {}).get("plan")
    agent_plan = agent if isinstance(agent, dict) else planned
    manual_plan = manual if isinstance(manual, dict) else {}
    result = dict(agent_plan)
    agent_actions = agent_plan.get("unitActions", agent_plan.get("unit_actions", {}))
    manual_actions = manual_plan.get("unitActions", manual_plan.get("unit_actions", {}))
    merged_actions = dict(agent_actions) if isinstance(agent_actions, dict) else {}
    if isinstance(manual_actions, dict):
        merged_actions.update(manual_actions)
    if "tick" not in result and "tick" in manual_plan:
        result["tick"] = manual_plan["tick"]
    result.pop("unit_actions", None)
    result["unitActions"] = merged_actions
    manual_core = manual_plan.get("coreAction", manual_plan.get("core_action"))
    if manual_core is not None:
        result.pop("core_action", None)
        result["coreAction"] = manual_core
    return result
