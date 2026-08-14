from __future__ import annotations

import sqlite3

import pytest

from app.storage import Database, ExplorationRepository
from app.strategy.exploration import (
    MASK_BYTES,
    ChunkKey,
    ExplorationChunk,
    ExplorationDelta,
)


def chunk(
    *,
    explored: int,
    obstacle: int = 0,
    tick: int = 7,
    key: ChunkKey = ChunkKey(0, 0),
) -> ExplorationChunk:
    explored_mask = bytearray(MASK_BYTES)
    obstacle_mask = bytearray(MASK_BYTES)
    explored_mask[0] = explored
    obstacle_mask[0] = obstacle
    return ExplorationChunk(
        key,
        bytes(explored_mask),
        bytes(obstacle_mask),
        tick,
        0,
    )


def test_merge_is_idempotent_and_revisions_only_change_for_new_bits(tmp_path) -> None:
    database = Database(tmp_path / "agent.db")
    database.initialize()
    repository = ExplorationRepository(database)

    first = repository.merge_delta(
        "account-a",
        ExplorationDelta(7, (chunk(explored=1),), (ChunkKey(0, 0),)),
    )
    repeated = repository.merge_delta(
        "account-a",
        ExplorationDelta(8, (chunk(explored=1),), (ChunkKey(0, 0),)),
    )
    expanded = repository.merge_delta(
        "account-a",
        ExplorationDelta(9, (chunk(explored=3),), (ChunkKey(0, 0),)),
    )

    assert (first, repeated, expanded) == (1, 1, 2)
    loaded_revision, loaded = repository.load_chunks(
        "account-a",
        (ChunkKey(0, 0),),
    )
    assert loaded_revision == 2
    assert loaded[0].explored_mask[0] == 3
    assert loaded[0].last_seen_tick == 9
    assert loaded[0].revision == 2


def test_touch_only_delta_advances_last_seen_without_incrementing_revision(tmp_path) -> None:
    database = Database(tmp_path / "agent.db")
    database.initialize()
    repository = ExplorationRepository(database)
    key = ChunkKey(0, 0)
    repository.merge_delta(
        "account-a",
        ExplorationDelta(4, (chunk(explored=1, tick=4),), (key,)),
    )

    revision = repository.merge_delta(
        "account-a",
        ExplorationDelta(12, (), (key,)),
    )
    loaded_revision, loaded = repository.load_chunks("account-a", (key,))

    assert revision == loaded_revision == 1
    assert loaded[0].last_seen_tick == 12
    assert loaded[0].revision == 1


def test_accounts_are_isolated_and_window_is_bounded_to_requested_scope(tmp_path) -> None:
    database = Database(tmp_path / "agent.db")
    database.initialize()
    repository = ExplorationRepository(database)
    repository.merge_delta(
        "account-a",
        ExplorationDelta(1, (chunk(explored=1),)),
    )
    repository.merge_delta(
        "account-b",
        ExplorationDelta(1, (chunk(explored=2),)),
    )

    account_a = repository.window(
        "account-a",
        min_x=0,
        min_y=0,
        max_x=1,
        max_y=0,
    )
    account_b = repository.window(
        "account-b",
        min_x=0,
        min_y=0,
        max_x=1,
        max_y=0,
    )

    assert account_a.explored_cells == ((0, 0),)
    assert account_b.explored_cells == ((1, 0),)


def test_corrupt_mask_is_rejected_by_sqlite_check(tmp_path) -> None:
    database = Database(tmp_path / "agent.db")
    database.initialize()
    repository = ExplorationRepository(database)

    with pytest.raises(sqlite3.IntegrityError):
        with database.connect() as connection:
            connection.execute(
                "INSERT INTO exploration_chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "account",
                    0,
                    0,
                    b"short",
                    bytes(MASK_BYTES),
                    1,
                    1,
                    "now",
                ),
            )
            connection.commit()

    assert repository.load_chunks("account", (ChunkKey(0, 0),))[1] == ()


def test_window_and_chunk_load_limits_fail_closed(tmp_path) -> None:
    database = Database(tmp_path / "agent.db")
    database.initialize()
    repository = ExplorationRepository(database)

    with pytest.raises(ValueError, match="64"):
        repository.load_chunks(
            "account-a",
            tuple(ChunkKey(index, 0) for index in range(65)),
        )
    with pytest.raises(ValueError, match="9216"):
        repository.window(
            "account-a",
            min_x=0,
            min_y=0,
            max_x=96,
            max_y=95,
        )
