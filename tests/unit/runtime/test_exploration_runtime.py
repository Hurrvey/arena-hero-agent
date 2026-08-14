from __future__ import annotations

import logging
from types import SimpleNamespace
from uuid import UUID

from app.runtime.exploration import ExplorationRuntime
from app.strategy.exploration import ChunkKey, ExplorationMap


class Repository:
    def __init__(self) -> None:
        self.loads: list[tuple[ChunkKey, ...]] = []
        self.saved = []

    def load_chunks(self, account_scope, keys, *, busy_timeout_ms=0):
        assert account_scope == "scope"
        assert busy_timeout_ms == 0
        self.loads.append(keys)
        return 0, ()

    def merge_delta(self, account_scope, delta):
        self.saved.append((account_scope, delta))
        return 1


def turn() -> SimpleNamespace:
    core = SimpleNamespace(
        id=UUID(int=1),
        kind="CORE",
        position=(0, 0),
        hp=5,
        shield=5,
    )
    worker = SimpleNamespace(
        id=UUID(int=2),
        kind="UNIT",
        position=(0, 3),
        hp=2,
        unit_type="WORKER",
    )
    return SimpleNamespace(
        tick=10,
        core=core,
        units=(worker,),
        obstacle_cells=frozenset({(1, 0)}),
    )


def memory() -> SimpleNamespace:
    return SimpleNamespace(
        exploration=ExplorationMap(),
        current_visible_cells=frozenset(),
        known_obstacles=set(),
        exploration_observed_tick=None,
    )


def test_observe_loads_bounded_working_set_and_marks_current_visibility() -> None:
    repository = Repository()
    runtime = ExplorationRuntime(repository, "scope", max_loaded_chunks=64)
    tactic_memory = memory()

    observation = runtime.observe_turn(turn(), tactic_memory)

    assert repository.loads
    assert len(repository.loads[0]) <= 64
    assert (0, 0) in observation.current_cells
    assert (1, 0) in observation.current_cells
    assert (2, 0) not in observation.current_cells
    assert tactic_memory.current_visible_cells == observation.current_cells
    assert tactic_memory.exploration_observed_tick == 10
    assert observation.delta.chunks


def test_persist_happens_only_when_explicitly_called_after_observation() -> None:
    repository = Repository()
    runtime = ExplorationRuntime(repository, "scope")
    observation = runtime.observe_turn(turn(), memory())
    assert repository.saved == []

    revision = runtime.persist(observation)

    assert revision == 1
    assert repository.saved == [("scope", observation.delta)]


def test_busy_repository_degrades_to_current_visibility_without_scope_in_log(
    caplog,
) -> None:
    class BusyRepository(Repository):
        def load_chunks(self, account_scope, keys, *, busy_timeout_ms=0):
            raise OSError("scope must never appear in a log")

    runtime = ExplorationRuntime(BusyRepository(), "scope")
    tactic_memory = memory()

    with caplog.at_level(logging.WARNING):
        observation = runtime.observe_turn(turn(), tactic_memory)

    assert (0, 0) in observation.current_cells
    assert observation.loaded_history is False
    assert "scope" not in caplog.text


def test_persistence_failure_returns_base_revision_and_is_fail_open(caplog) -> None:
    class FailingRepository(Repository):
        def load_chunks(self, account_scope, keys, *, busy_timeout_ms=0):
            return 7, ()

        def merge_delta(self, account_scope, delta):
            raise OSError("private persistence detail")

    runtime = ExplorationRuntime(FailingRepository(), "scope")
    observation = runtime.observe_turn(turn(), memory())

    with caplog.at_level(logging.WARNING):
        revision = runtime.persist(observation)

    assert revision == 7
    assert caplog.messages[-1] == "exploration persistence degraded"
    assert "private persistence detail" not in caplog.text


def test_successful_revision_never_regresses_after_a_later_write_failure() -> None:
    class IntermittentRepository(Repository):
        def __init__(self) -> None:
            super().__init__()
            self.fail = False

        def merge_delta(self, account_scope, delta):
            if self.fail:
                raise OSError("temporary")
            return 3

    repository = IntermittentRepository()
    runtime = ExplorationRuntime(repository, "scope")
    first = runtime.observe_turn(turn(), memory())
    assert runtime.persist(first) == 3
    repository.fail = True

    next_turn = turn()
    next_turn.tick = 11
    second = runtime.observe_turn(next_turn, memory())

    assert second.base_revision == 3
    assert runtime.persist(second) == 3
