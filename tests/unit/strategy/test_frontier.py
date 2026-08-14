from __future__ import annotations

import pytest

from app.strategy.exploration import ExplorationMap
from app.strategy.frontier import (
    FrontierMemory,
    FrontierSettings,
    ScoutSnapshot,
    assign_frontiers,
    frontier_cells,
    next_frontier_step,
    record_scout_observation,
)
from app.strategy.models import CellRisk


def explored_rectangle(min_x: int, min_y: int, max_x: int, max_y: int) -> ExplorationMap:
    exploration = ExplorationMap()
    cells = frozenset(
        (x, y)
        for x in range(min_x, max_x + 1)
        for y in range(min_y, max_y + 1)
    )
    exploration.observe(
        visible_cells=cells,
        visible_obstacles=frozenset(),
        tick=1,
    )
    return exploration


def test_frontier_is_explored_passable_and_cardinally_adjacent_to_unknown() -> None:
    exploration = explored_rectangle(0, 0, 2, 2)
    cells = frontier_cells(
        exploration,
        min_x=0,
        min_y=0,
        max_x=2,
        max_y=2,
        obstacles=frozenset({(1, 0)}),
        limit=64,
    )

    assert (1, 1) not in cells
    assert (1, 0) not in cells
    assert (0, 0) in cells
    assert (2, 2) in cells


def test_two_workers_receive_distinct_low_overlap_frontiers_deterministically() -> None:
    exploration = explored_rectangle(-2, -2, 2, 2)
    workers = (
        ScoutSnapshot(b"a", (0, 0)),
        ScoutSnapshot(b"b", (0, 1)),
    )
    memory = FrontierMemory()
    assignments = assign_frontiers(
        memory,
        workers,
        exploration=exploration,
        risk_map={},
        obstacles=frozenset(),
        occupied=frozenset(),
        tick=5,
        settings=FrontierSettings(),
    )
    repeated = assign_frontiers(
        memory,
        tuple(reversed(workers)),
        exploration=exploration,
        risk_map={},
        obstacles=frozenset(),
        occupied=frozenset(),
        tick=5,
        settings=FrontierSettings(),
    )

    assert assignments == repeated
    assert len({item.target for item in assignments.values()}) == 2


def test_existing_lease_stays_until_completed_invalid_or_stalled() -> None:
    exploration = explored_rectangle(0, 0, 4, 4)
    memory = FrontierMemory()
    first = assign_frontiers(
        memory,
        (ScoutSnapshot(b"a", (2, 2)),),
        exploration=exploration,
        risk_map={},
        obstacles=frozenset(),
        occupied=frozenset(),
        tick=10,
        settings=FrontierSettings(),
    )[b"a"]
    second = assign_frontiers(
        memory,
        (ScoutSnapshot(b"a", (2, 3)),),
        exploration=exploration,
        risk_map={},
        obstacles=frozenset(),
        occupied=frozenset(),
        tick=11,
        settings=FrontierSettings(),
    )[b"a"]

    assert second.target == first.target
    assert second.reason_code == "SCOUT_FRONTIER"


def test_a_b_a_marks_the_reverse_edge_taboo_and_reassigns() -> None:
    memory = FrontierMemory()
    settings = FrontierSettings(edge_cooldown_ticks=4)
    for tick, position in enumerate(((0, 0), (1, 0), (0, 0)), start=1):
        record_scout_observation(
            memory,
            b"a",
            position,
            explored_count=tick,
            tick=tick,
            settings=settings,
        )

    assert memory.taboo_edges[(b"a", (0, 0), (1, 0))] == 7
    assert memory.leases.get(b"a") is None
    assert memory.oscillation_detections == 1


def test_a_b_c_b_and_no_coverage_progress_also_release_the_lease() -> None:
    memory = FrontierMemory()
    settings = FrontierSettings(lease_stall_ticks=3)
    memory.ensure_lease(
        b"a",
        target=(9, 9),
        distance=10,
        explored_count=4,
        tick=1,
    )
    for tick, position in enumerate(((0, 0), (1, 0), (2, 0), (1, 0)), start=2):
        record_scout_observation(
            memory,
            b"a",
            position,
            explored_count=4,
            tick=tick,
            settings=settings,
        )

    assert b"a" not in memory.leases
    assert memory.oscillation_detections == 1


def test_route_avoids_taboo_risk_obstacle_occupancy_and_can_return_wait() -> None:
    memory = FrontierMemory()
    memory.taboo_edges[(b"a", (0, 0), (1, 0))] = 9
    step = next_frontier_step(
        ScoutSnapshot(b"a", (0, 0)),
        target=(2, 0),
        memory=memory,
        risk_map={(0, -1): CellRisk(1, 1, (b"enemy",))},
        obstacles=frozenset({(0, 1)}),
        occupied=frozenset({(-1, 0)}),
        reserved=frozenset(),
        tick=5,
        max_expansions=64,
    )

    assert step is None
    assert memory.oscillation_prevented_moves == 1
    assert memory.scout_wait_ticks == 1


def test_duplicate_tick_observation_is_idempotent() -> None:
    memory = FrontierMemory()
    settings = FrontierSettings()
    record_scout_observation(
        memory,
        b"a",
        (0, 0),
        explored_count=1,
        tick=1,
        settings=settings,
    )
    record_scout_observation(
        memory,
        b"a",
        (1, 0),
        explored_count=2,
        tick=1,
        settings=settings,
    )

    assert tuple(memory.histories[b"a"]) == ((0, 0),)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"search_radius": 0},
        {"candidate_limit": 0},
        {"route_expansions": 0},
        {"lease_stall_ticks": 0},
        {"edge_cooldown_ticks": 0},
    ],
)
def test_frontier_settings_require_positive_limits(kwargs: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        FrontierSettings(**kwargs)
