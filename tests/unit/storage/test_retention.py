from datetime import UTC, datetime, timedelta

from app.storage.database import Database
from app.storage.retention import RetentionService
from app.storage.runtime_store import RuntimeStore


def test_retention_prunes_old_raw_and_event_rows_but_keeps_recent_data(tmp_path) -> None:
    database = Database(tmp_path / "agent.db")
    database.initialize()
    store = RuntimeStore(database)
    session = store.create_session(account_hash="account")
    for tick in (1, 2):
        store.save_turn_batch(
            session_id=session.session_id,
            tick=tick,
            raw_snapshot={"tick": tick},
            public_snapshot={"tick": tick},
            raw_plan={"private": tick},
            public_plan={},
            explanation={},
            resolution_events=(
                {
                    "plan_tick": max(0, tick - 1),
                    "event_type": "MOVE_SUCCEEDED",
                },
            ),
            service_events=(("turn.observed", {}),),
        )
    old_raw = (datetime.now(UTC) - timedelta(days=8)).isoformat()
    old_event = (datetime.now(UTC) - timedelta(days=31)).isoformat()
    with database.connect() as connection:
        connection.execute(
            "UPDATE turn_snapshots SET received_at = ? WHERE tick = 1",
            (old_raw,),
        )
        connection.execute(
            "UPDATE resolution_events SET created_at = ? WHERE observed_tick = 1",
            (old_event,),
        )
        connection.execute(
            "UPDATE service_events SET created_at = ? WHERE tick = 1",
            (old_event,),
        )
        connection.commit()

    deleted = RetentionService(database).prune(raw_days=7, event_days=30)

    with database.connect() as connection:
        snapshot_ticks = connection.execute(
            "SELECT tick, raw_payload_json FROM turn_snapshots ORDER BY tick"
        ).fetchall()
        resolution_ticks = connection.execute(
            "SELECT observed_tick FROM resolution_events ORDER BY observed_tick"
        ).fetchall()
        service_ticks = connection.execute(
            "SELECT tick FROM service_events ORDER BY tick"
        ).fetchall()
        raw_plans = connection.execute(
            "SELECT tick, raw_plan_json FROM plans ORDER BY tick"
        ).fetchall()
    assert deleted == 4
    assert snapshot_ticks == [(1, "{}"), (2, '{"tick":2}')]
    assert resolution_ticks == [(2,)]
    assert service_ticks == [(2,)]
    assert raw_plans == [(1, "{}"), (2, '{"private":2}')]


def test_prune_all_drains_more_than_one_bounded_batch(tmp_path) -> None:
    database = Database(tmp_path / "agent.db")
    database.initialize()
    store = RuntimeStore(database)
    session = store.create_session(account_hash="account")
    old_event = (datetime.now(UTC) - timedelta(days=31)).isoformat()
    with database.connect() as connection:
        connection.executemany(
            """
            INSERT INTO service_events(session_id, tick, event_type, payload_json, created_at)
            VALUES (?, ?, 'old', '{}', ?)
            """,
            ((session.session_id, tick, old_event) for tick in range(1, 1202)),
        )
        connection.commit()

    deleted = RetentionService(database).prune_all(raw_days=7, event_days=30, batch=500)

    with database.connect() as connection:
        remaining = connection.execute("SELECT count(*) FROM service_events").fetchone()[0]
    assert deleted == 1201
    assert remaining == 0
