from __future__ import annotations

import pytest

from app.strategy.exploration import (
    MASK_BYTES,
    ChunkKey,
    ExplorationChunk,
    ExplorationMap,
    bit_index,
    chunk_key,
)


def test_negative_coordinates_have_stable_chunk_and_bit_indexes() -> None:
    assert chunk_key((-1, -1)) == ChunkKey(-1, -1)
    assert bit_index((-1, -1)) == 1023
    assert chunk_key((-32, -32)) == ChunkKey(-1, -1)
    assert bit_index((-32, -32)) == 0
    assert chunk_key((-33, 0)) == ChunkKey(-2, 0)
    assert bit_index((-33, 0)) == 31


def test_observation_is_monotonic_and_returns_only_changed_chunks() -> None:
    exploration = ExplorationMap()
    first = exploration.observe(
        visible_cells=frozenset({(-1, -1), (0, 0), (1, 0)}),
        visible_obstacles=frozenset({(1, 0)}),
        tick=10,
    )
    second = exploration.observe(
        visible_cells=frozenset({(0, 0), (1, 0)}),
        visible_obstacles=frozenset({(1, 0)}),
        tick=11,
    )

    assert {item.key for item in first.chunks} == {
        ChunkKey(-1, -1),
        ChunkKey(0, 0),
    }
    assert second.chunks == ()
    assert second.touched_keys == (ChunkKey(0, 0),)
    assert exploration.is_explored((-1, -1))
    assert exploration.is_explored((0, 0))
    assert exploration.is_known_obstacle((1, 0))


def test_loaded_chunks_merge_by_or_without_erasing_newer_bits() -> None:
    exploration = ExplorationMap()
    exploration.observe(
        visible_cells=frozenset({(1, 1)}),
        visible_obstacles=frozenset(),
        tick=12,
    )
    old_mask = bytearray(MASK_BYTES)
    old_mask[0] = 1
    exploration.merge_loaded(
        (
            ExplorationChunk(
                key=ChunkKey(0, 0),
                explored_mask=bytes(old_mask),
                obstacle_mask=bytes(MASK_BYTES),
                last_seen_tick=5,
                revision=2,
            ),
        ),
        account_revision=2,
    )

    assert exploration.is_explored((0, 0))
    assert exploration.is_explored((1, 1))
    assert exploration.account_revision == 2


def test_window_contains_only_requested_explored_and_obstacle_cells() -> None:
    exploration = ExplorationMap()
    exploration.observe(
        visible_cells=frozenset({(-1, 0), (0, 0), (3, 3), (50, 50)}),
        visible_obstacles=frozenset({(3, 3)}),
        tick=20,
    )

    window = exploration.window(min_x=-1, min_y=0, max_x=3, max_y=3)

    assert window.explored_cells == ((-1, 0), (0, 0), (3, 3))
    assert window.known_obstacle_cells == ((3, 3),)


def test_invalid_masks_and_unexplored_obstacles_are_rejected() -> None:
    with pytest.raises(ValueError, match="exactly 128 bytes"):
        ExplorationChunk(
            key=ChunkKey(0, 0),
            explored_mask=b"short",
            obstacle_mask=bytes(MASK_BYTES),
            last_seen_tick=0,
            revision=0,
        )

    obstacle_mask = bytearray(MASK_BYTES)
    obstacle_mask[0] = 1
    with pytest.raises(ValueError, match="also be explored"):
        ExplorationChunk(
            key=ChunkKey(0, 0),
            explored_mask=bytes(MASK_BYTES),
            obstacle_mask=bytes(obstacle_mask),
            last_seen_tick=0,
            revision=0,
        )


def test_evict_except_keeps_requested_chunks_and_respects_limit() -> None:
    exploration = ExplorationMap()
    exploration.observe(
        visible_cells=frozenset({(0, 0), (32, 0), (64, 0)}),
        visible_obstacles=frozenset(),
        tick=3,
    )

    exploration.evict_except(frozenset({ChunkKey(2, 0)}), max_chunks=2)

    assert ChunkKey(2, 0) in exploration.loaded_keys()
    assert len(exploration.loaded_keys()) == 2
