import sqlite3

import pytest

from app.storage.database import Database
from app.storage.runtime_store import RuntimeStore


def test_open_enables_wal_foreign_keys_and_busy_timeout(tmp_path) -> None:
    database = Database(tmp_path / "agent.db")
    database.initialize()

    with database.connect() as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 1


def test_migrations_are_idempotent(tmp_path) -> None:
    database = Database(tmp_path / "agent.db")

    database.initialize()
    database.initialize()

    with database.connect() as connection:
        versions = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
    assert versions == [(1,), (2,), (3,)]


def test_turn_batch_and_service_events_commit_atomically(tmp_path) -> None:
    database = Database(tmp_path / "agent.db")
    database.initialize()
    store = RuntimeStore(database)
    session = store.create_session(account_hash="account")

    with pytest.raises(sqlite3.IntegrityError):
        store.save_turn_batch(
            session_id=session.session_id,
            tick=1,
            raw_snapshot={"tick": 1},
            public_snapshot={"tick": 1},
            raw_plan={"tick": 1},
            public_plan={"tick": 1},
            explanation={},
            resolution_events=(),
            service_events=(
                ("TURN_RECEIVED", {"tick": 1}),
                (None, {"invalid": True}),
            ),
        )

    with database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM turn_snapshots").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM plans").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM service_events").fetchone()[0] == 0


def test_events_after_returns_monotonic_seq_and_limit(tmp_path) -> None:
    database = Database(tmp_path / "agent.db")
    database.initialize()
    store = RuntimeStore(database)
    session = store.create_session(account_hash="account")
    store.save_turn_batch(
        session_id=session.session_id,
        tick=1,
        raw_snapshot={"tick": 1},
        public_snapshot={"tick": 1},
        raw_plan={"tick": 1},
        public_plan={"tick": 1},
        explanation={},
        resolution_events=(),
        service_events=(("ONE", {}), ("TWO", {}), ("THREE", {})),
    )

    page = store.events_after(1, limit=2)

    assert [event.seq for event in page.events] == [2, 3]
    assert page.last_seq == 3
    assert store.latest_event_seq() == 3


def test_receipt_and_next_turn_resolution_advance_plan_lifecycle(tmp_path) -> None:
    database = Database(tmp_path / "agent.db")
    database.initialize()
    store = RuntimeStore(database)
    session = store.create_session(account_hash="account")
    store.save_turn_batch(
        session_id=session.session_id,
        tick=7,
        raw_snapshot={"tick": 7},
        public_snapshot={"tick": 7},
        raw_plan={"actions": [{"type": "HARVEST"}]},
        public_plan={"actions": [{"type": "HARVEST"}]},
        explanation={},
        resolution_events=(),
        service_events=(("plan.accepted", {}),),
        plan_status="ACCEPTED",
    )

    receipt_event = store.save_receipt(
        session_id=session.session_id,
        tick=7,
        receipt={"accepted": True, "source": "AGENT"},
    )
    store.save_turn_batch(
        session_id=session.session_id,
        tick=8,
        raw_snapshot={"tick": 8},
        public_snapshot={"tick": 8},
        raw_plan={},
        public_plan={},
        explanation={},
        resolution_events=(
            {
                "plan_tick": 7,
                "event_type": "HARVEST_SUCCEEDED",
                "short_id": "E1",
                "values": {"amount": 1},
            },
        ),
        service_events=(("action.resolved", {"eventType": "HARVEST_SUCCEEDED"}),),
        resolve_plan_tick=7,
    )

    plan = store.plan_at(session.session_id, 7)
    assert receipt_event.event_type == "plan.accepted"
    assert plan is not None
    assert plan["status"] == "RESOLVED"
    assert plan["receipt"] == {"accepted": True, "source": "AGENT"}
    assert plan["resolutionEvents"] == [
        {
            "event_type": "HARVEST_SUCCEEDED",
            "plan_tick": 7,
            "short_id": "E1",
            "values": {"amount": 1},
        }
    ]


def test_official_received_plan_updates_the_matching_draft_plan(tmp_path) -> None:
    database = Database(tmp_path / "agent.db")
    database.initialize()
    store = RuntimeStore(database)
    session = store.create_session(account_hash="account")
    store.save_turn_batch(
        session_id=session.session_id,
        tick=9,
        raw_snapshot={"tick": 9},
        public_snapshot={"tick": 9},
        raw_plan={},
        public_plan={},
        explanation={},
        resolution_events=(),
        service_events=(("turn.observed", {}),),
        plan_status="DRAFT",
    )

    store.save_receipt(
        session_id=session.session_id,
        tick=9,
        receipt={"accepted": True, "source": "MANUAL"},
        raw_plan={"tick": 9, "unit_actions": {"private": {"type": "WAIT"}}},
        public_plan={"tick": 9, "unitActions": {"E1": {"type": "WAIT"}}},
    )

    plan = store.plan_at(session.session_id, 9)
    assert plan is not None
    assert plan["status"] == "ACCEPTED"
    assert plan["plan"] == {"tick": 9, "unitActions": {"E1": {"type": "WAIT"}}}


def test_agent_and_manual_receipts_keep_separate_slots_and_effective_plan(tmp_path) -> None:
    database = Database(tmp_path / "agent.db")
    database.initialize()
    store = RuntimeStore(database)
    session = store.create_session(account_hash="account")
    store.save_turn_batch(
        session_id=session.session_id,
        tick=10,
        raw_snapshot={"tick": 10},
        public_snapshot={"tick": 10},
        raw_plan={},
        public_plan={},
        explanation={},
        resolution_events=(),
        service_events=(("state.snapshot", {}),),
        plan_status="DRAFT",
    )
    store.save_receipt(
        session_id=session.session_id,
        tick=10,
        receipt={"accepted": True, "source": "AGENT"},
        raw_plan={"tick": 10, "unit_actions": {"raw-agent": {"type": "HARVEST"}}},
        public_plan={"tick": 10, "unitActions": {"E1": {"type": "HARVEST"}}},
    )
    store.save_receipt(
        session_id=session.session_id,
        tick=10,
        receipt={"accepted": True, "source": "MANUAL"},
        raw_plan={"tick": 10, "unit_actions": {"raw-manual": {"type": "WAIT"}}},
        public_plan={"tick": 10, "unitActions": {"E2": {"type": "WAIT"}}},
    )

    plan = store.plan_at(session.session_id, 10)

    assert plan is not None
    assert set(plan["receipts"]) == {"AGENT", "MANUAL"}
    assert plan["receipts"]["AGENT"]["plan"]["unitActions"] == {"E1": {"type": "HARVEST"}}
    assert plan["receipts"]["MANUAL"]["plan"]["unitActions"] == {"E2": {"type": "WAIT"}}
    assert plan["plan"]["unitActions"] == {
        "E1": {"type": "HARVEST"},
        "E2": {"type": "WAIT"},
    }


def test_replayed_receipt_can_arrive_before_the_turn_snapshot(tmp_path) -> None:
    database = Database(tmp_path / "agent.db")
    database.initialize()
    store = RuntimeStore(database)
    session = store.create_session(account_hash="account")

    store.save_receipt(
        session_id=session.session_id,
        tick=11,
        receipt={"accepted": True, "source": "MANUAL"},
        raw_plan={"tick": 11, "unit_actions": {}},
        public_plan={"tick": 11, "unitActions": {}},
    )
    store.save_turn_batch(
        session_id=session.session_id,
        tick=11,
        raw_snapshot={"tick": 11},
        public_snapshot={"tick": 11},
        raw_plan={},
        public_plan={},
        explanation={},
        resolution_events=(),
        service_events=(("state.snapshot", {}),),
    )

    plan = store.plan_at(session.session_id, 11)
    assert plan is not None
    assert plan["receipts"]["MANUAL"]["plan"] == {"tick": 11, "unitActions": {}}
