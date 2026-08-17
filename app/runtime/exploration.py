"""Bounded runtime bridge between authoritative Turns and exploration storage."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass

from app.storage import ExplorationRepository
from app.strategy.exploration import (
    ChunkKey,
    ExplorationChunk,
    ExplorationDelta,
    chunk_key,
)
from app.strategy.models import Position, entity_snapshot_from_view
from app.strategy.visibility import compute_visible_cells

logger = logging.getLogger(__name__)

_MAX_LOAD_KEYS = 64


@dataclass(frozen=True, slots=True)
class ExplorationObservation:
    tick: int
    current_cells: frozenset[Position]
    delta: ExplorationDelta
    base_revision: int
    loaded_history: bool


class ExplorationRuntime:
    def __init__(
        self,
        repository: ExplorationRepository,
        account_scope: str,
        *,
        max_loaded_chunks: int = 256,
        load_radius: int = 48,
    ) -> None:
        if not isinstance(account_scope, str) or not account_scope:
            raise ValueError("account_scope must be a non-empty string")
        if (
            not isinstance(max_loaded_chunks, int)
            or isinstance(max_loaded_chunks, bool)
            or max_loaded_chunks < 1
        ):
            raise ValueError("max_loaded_chunks must be a positive integer")
        if not isinstance(load_radius, int) or isinstance(load_radius, bool) or load_radius < 0:
            raise ValueError("load_radius must be a non-negative integer")
        self._repository = repository
        self._account_scope = account_scope
        self._max_loaded_chunks = max_loaded_chunks
        self._load_radius = load_radius
        self._last_revision = 0
        self._pending_delta: ExplorationDelta | None = None

    def observe_turn(self, turn: object, memory: object) -> ExplorationObservation:
        tick = int(getattr(turn, "tick", 0))
        exploration = getattr(memory, "exploration")
        entities = self._controlled_entities(turn)
        missing_keys = self._load_keys(entities, exploration.loaded_keys())
        loaded_history = True
        if missing_keys:
            try:
                account_revision, chunks = self._repository.load_chunks(
                    self._account_scope,
                    missing_keys,
                    busy_timeout_ms=0,
                )
                exploration.merge_loaded(chunks, account_revision=account_revision)
                self._last_revision = max(self._last_revision, account_revision)
            except (sqlite3.Error, OSError, ValueError):
                loaded_history = False
                logger.warning("exploration history load degraded")

        current_obstacles = frozenset(
            tuple(position)
            for position in (getattr(turn, "obstacle_cells", ()) or ())
        )
        visibility_obstacles = frozenset(
            set(current_obstacles) | set(exploration.known_obstacle_cells())
        )
        current_cells = compute_visible_cells(entities, visibility_obstacles)
        memory.newly_explored_cells = sum(
            not exploration.is_explored(position) for position in current_cells
        )
        delta = exploration.observe(
            visible_cells=current_cells,
            visible_obstacles=current_obstacles,
            tick=tick,
        )

        current_keys = {chunk_key(position) for position in current_cells}
        entity_keys = {chunk_key(entity.position) for entity in entities}
        prioritized_keep = tuple(
            sorted(entity_keys)
            + sorted(current_keys - entity_keys)
        )[: self._max_loaded_chunks]
        exploration.evict_except(
            frozenset(prioritized_keep),
            max_chunks=self._max_loaded_chunks,
        )
        memory.current_visible_cells = current_cells
        memory.exploration_observed_tick = tick
        memory.known_obstacles = set(current_obstacles) | set(
            exploration.known_obstacle_cells()
        )
        return ExplorationObservation(
            tick=tick,
            current_cells=current_cells,
            delta=delta,
            base_revision=max(exploration.account_revision, self._last_revision),
            loaded_history=loaded_history,
        )

    def persist(self, observation: ExplorationObservation) -> int:
        delta = _merge_deltas(self._pending_delta, observation.delta)
        try:
            revision = self._repository.merge_delta(
                self._account_scope,
                delta,
            )
            self._pending_delta = None
            self._last_revision = max(self._last_revision, revision)
            return self._last_revision
        except (sqlite3.Error, OSError, ValueError):
            self._pending_delta = delta
            logger.warning("exploration persistence degraded")
            return max(observation.base_revision, self._last_revision)

    @staticmethod
    def _controlled_entities(turn: object):
        entities = []
        core = getattr(turn, "core", None)
        if core is not None:
            snapshot = entity_snapshot_from_view(core, controlled=True)
            if snapshot is not None:
                entities.append(snapshot)
        for unit in getattr(turn, "units", ()) or ():
            snapshot = entity_snapshot_from_view(unit, controlled=True)
            if snapshot is not None:
                entities.append(snapshot)
        return tuple(sorted(entities, key=lambda item: item.entity_id))

    def _load_keys(self, entities, loaded: frozenset[ChunkKey]) -> tuple[ChunkKey, ...]:
        candidates: set[ChunkKey] = set()
        for entity in entities:
            min_x = (entity.position[0] - self._load_radius) // 32
            max_x = (entity.position[0] + self._load_radius) // 32
            min_y = (entity.position[1] - self._load_radius) // 32
            max_y = (entity.position[1] + self._load_radius) // 32
            candidates.update(
                ChunkKey(x, y)
                for x in range(min_x, max_x + 1)
                for y in range(min_y, max_y + 1)
            )

        def rank(key: ChunkKey) -> tuple[int, int, int]:
            center = (key.x * 32 + 15, key.y * 32 + 15)
            distance = min(
                (
                    abs(entity.position[0] - center[0])
                    + abs(entity.position[1] - center[1])
                    for entity in entities
                ),
                default=0,
            )
            return distance, key.x, key.y

        return tuple(sorted(candidates - set(loaded), key=rank)[:_MAX_LOAD_KEYS])


def _merge_deltas(
    pending: ExplorationDelta | None,
    current: ExplorationDelta,
) -> ExplorationDelta:
    if pending is None:
        return current
    chunks: dict[ChunkKey, ExplorationChunk] = {
        chunk.key: chunk for chunk in pending.chunks
    }
    for incoming in current.chunks:
        prior = chunks.get(incoming.key)
        if prior is None:
            chunks[incoming.key] = incoming
            continue
        chunks[incoming.key] = ExplorationChunk(
            key=incoming.key,
            explored_mask=bytes(
                left | right
                for left, right in zip(
                    prior.explored_mask,
                    incoming.explored_mask,
                    strict=True,
                )
            ),
            obstacle_mask=bytes(
                left | right
                for left, right in zip(
                    prior.obstacle_mask,
                    incoming.obstacle_mask,
                    strict=True,
                )
            ),
            last_seen_tick=max(prior.last_seen_tick, incoming.last_seen_tick),
            revision=max(prior.revision, incoming.revision),
        )
    return ExplorationDelta(
        tick=max(pending.tick, current.tick),
        chunks=tuple(chunks[key] for key in sorted(chunks)),
        touched_keys=tuple(sorted(set(pending.touched_keys) | set(current.touched_keys))),
    )
