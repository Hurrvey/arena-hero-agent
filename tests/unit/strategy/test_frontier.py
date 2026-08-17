from __future__ import annotations

import pytest

from app.strategy import frontier as frontier_module
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


def test_distant_scouts_scan_only_their_own_bounded_windows(monkeypatch) -> None:
    exploration = ExplorationMap()
    exploration.observe(
        visible_cells=frozenset(
            {
                (x, y)
                for center_x, center_y in ((0, 0), (1000, 1000))
                for x in range(center_x - 3, center_x + 4)
                for y in range(center_y - 3, center_y + 4)
            }
        ),
        visible_obstacles=frozenset(),
        tick=1,
    )
    settings = FrontierSettings(search_radius=8, candidate_limit=32)
    calls: list[tuple[int, int, int, int]] = []
    real_frontier_cells = frontier_module.frontier_cells

    def bounded_frontier_cells(exploration, **kwargs):
        calls.append(
            (
                kwargs["min_x"],
                kwargs["min_y"],
                kwargs["max_x"],
                kwargs["max_y"],
            )
        )
        return real_frontier_cells(exploration, **kwargs)

    monkeypatch.setattr(frontier_module, "frontier_cells", bounded_frontier_cells)

    assignments = assign_frontiers(
        FrontierMemory(),
        (
            ScoutSnapshot(b"a", (0, 0)),
            ScoutSnapshot(b"b", (1000, 1000)),
        ),
        exploration=exploration,
        risk_map={},
        obstacles=frozenset(),
        occupied=frozenset(),
        tick=5,
        settings=settings,
    )

    assert set(assignments) == {b"a", b"b"}
    assert len(calls) == 2
    assert all(max_x - min_x <= 16 for min_x, _min_y, max_x, _max_y in calls)
    assert all(max_y - min_y <= 16 for _min_x, min_y, _max_x, max_y in calls)


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


def test_inactive_scout_state_is_pruned_even_when_no_scouts_remain() -> None:
    memory = FrontierMemory()
    settings = FrontierSettings()
    memory.ensure_lease(
        b"dead",
        target=(3, 0),
        distance=3,
        explored_count=1,
        tick=1,
    )
    record_scout_observation(
        memory,
        b"dead",
        (0, 0),
        explored_count=1,
        tick=1,
        settings=settings,
    )
    memory.taboo_edges[(b"dead", (0, 0), (1, 0))] = 99
    memory.failed_targets[(b"dead", (3, 0))] = 99

    assert assign_frontiers(
        memory,
        (),
        exploration=ExplorationMap(),
        risk_map={},
        obstacles=frozenset(),
        occupied=frozenset(),
        tick=2,
        settings=settings,
    ) == {}

    assert memory.leases == {}
    assert memory.histories == {}
    assert memory.observed_ticks == {}
    assert memory.taboo_edges == {}
    assert memory.failed_targets == {}


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
