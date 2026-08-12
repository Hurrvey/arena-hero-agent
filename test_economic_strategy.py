from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

from economic_strategy import (
    EconomyMemory,
    EconomySettings,
    advance_stalled_targets,
    assign_resource_targets,
    detect_two_cell_oscillation,
    refresh_economy_memory,
    scout_targets,
    update_runner_lease,
)


def worker(number: int, position: tuple[int, int]):
    return SimpleNamespace(id=UUID(int=number), position=position)


def test_visible_disappearance_and_ttl_remove_resource_hints() -> None:
    memory = EconomyMemory()
    settings = EconomySettings(resource_memory_ttl=4)
    one = worker(1, (0, 0))

    refresh_economy_memory(
        memory,
        tick=10,
        workers=(one,),
        visible_resources={(1, 0), (9, 9)},
        friendly_positions=((0, 0),),
        settings=settings,
    )
    refresh_economy_memory(
        memory,
        tick=11,
        workers=(one,),
        visible_resources={(9, 9)},
        friendly_positions=((0, 0),),
        settings=settings,
    )

    assert (1, 0) not in memory.resource_last_seen
    assert memory.resource_last_seen[(9, 9)] == 11

    refresh_economy_memory(
        memory,
        tick=16,
        workers=(one,),
        visible_resources=set(),
        friendly_positions=((0, 0),),
        settings=settings,
    )

    assert memory.resource_last_seen == {}


def test_resource_assignment_is_one_to_one_and_minimum_cost() -> None:
    memory = EconomyMemory(resource_last_seen={(-5, 0): 20, (2, 0): 20})
    first = worker(1, (0, 0))
    second = worker(2, (-4, 0))

    assignments = assign_resource_targets(
        memory,
        (first, second),
        tick=20,
        blocked=set(),
    )

    assert assignments[first.id.bytes] == (2, 0)
    assert assignments[second.id.bytes] == (-5, 0)
    assert len(set(assignments.values())) == 2


def test_workers_without_resources_receive_distinct_scout_targets() -> None:
    memory = EconomyMemory()
    settings = EconomySettings(scout_ring_step=10)
    first = worker(1, (0, 0))
    second = worker(2, (0, 0))

    targets = scout_targets(
        memory,
        (first, second),
        core_position=(0, 0),
        tick=50,
        settings=settings,
    )

    assert targets[first.id.bytes] == (10, 0)
    assert targets[second.id.bytes] == (5, 5)
    assert len(set(targets.values())) == 2


def test_two_cell_oscillation_is_detected_from_four_positions() -> None:
    assert detect_two_cell_oscillation(((0, 0), (1, 0), (0, 0), (1, 0)))
    assert not detect_two_cell_oscillation(((0, 0), (1, 0), (2, 0), (3, 0)))
    assert not detect_two_cell_oscillation(((0, 0), (1, 0), (0, 0)))


def test_stalled_resource_and_scout_targets_advance_after_threshold() -> None:
    memory = EconomyMemory(resource_last_seen={(5, 0): 1})
    settings = EconomySettings(
        resource_stall_ticks=2,
        resource_cooldown_ticks=4,
        scout_stall_ticks=2,
    )
    one = worker(1, (0, 0))
    worker_key = one.id.bytes
    memory.resource_intents[worker_key] = (5, 0)
    memory.scout_slots[worker_key] = 0
    memory.scout_stages[worker_key] = 0

    advance_stalled_targets(
        memory,
        (one,),
        tick=10,
        blocked=set(),
        scout_assignments={worker_key: (10, 0)},
        settings=settings,
    )
    advance_stalled_targets(
        memory,
        (one,),
        tick=11,
        blocked=set(),
        scout_assignments={worker_key: (10, 0)},
        settings=settings,
    )
    advance_stalled_targets(
        memory,
        (one,),
        tick=12,
        blocked=set(),
        scout_assignments={worker_key: (10, 0)},
        settings=settings,
    )

    assert worker_key not in memory.resource_intents
    assert memory.resource_cooldowns[(worker_key, (5, 0))] == 16
    assert memory.scout_stages[worker_key] == 1


def test_runner_lease_releases_after_no_progress_and_starts_cooldown() -> None:
    memory = EconomyMemory()
    one = worker(1, (5, 0))
    worker_key = one.id.bytes

    assert update_runner_lease(
        memory,
        runner=one,
        target=(10, 0),
        tick=1,
        stall_limit=2,
    )
    assert update_runner_lease(
        memory,
        runner=one,
        target=(10, 0),
        tick=2,
        stall_limit=2,
    )
    assert not update_runner_lease(
        memory,
        runner=one,
        target=(10, 0),
        tick=3,
        stall_limit=2,
    )
    assert memory.runner_lease is None
    assert memory.runner_cooldowns[worker_key] > 3
