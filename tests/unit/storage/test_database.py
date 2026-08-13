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
    assert versions == [(1,), (2,)]


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
