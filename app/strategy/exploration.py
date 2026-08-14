"""Sparse, monotonic exploration memory for the unbounded Arena Hero map."""

from __future__ import annotations

from dataclasses import dataclass

from .models import Position, validate_position

CHUNK_SIZE = 32
MASK_BYTES = CHUNK_SIZE * CHUNK_SIZE // 8


def _validate_non_negative_integer(name: str, value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


@dataclass(frozen=True, order=True, slots=True)
class ChunkKey:
    x: int
    y: int

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, int) or isinstance(value, bool)
            for value in (self.x, self.y)
        ):
            raise ValueError("chunk coordinates must be integers")


@dataclass(frozen=True, slots=True)
class ExplorationChunk:
    key: ChunkKey
    explored_mask: bytes
    obstacle_mask: bytes
    last_seen_tick: int
    revision: int

    def __post_init__(self) -> None:
        if not isinstance(self.key, ChunkKey):
            raise TypeError("key must be a ChunkKey")
        if (
            not isinstance(self.explored_mask, bytes)
            or not isinstance(self.obstacle_mask, bytes)
            or len(self.explored_mask) != MASK_BYTES
            or len(self.obstacle_mask) != MASK_BYTES
        ):
            raise ValueError("exploration masks must contain exactly 128 bytes")
        _validate_non_negative_integer("last_seen_tick", self.last_seen_tick)
        _validate_non_negative_integer("revision", self.revision)
        if any(
            obstacle & ~explored
            for obstacle, explored in zip(
                self.obstacle_mask,
                self.explored_mask,
                strict=True,
            )
        ):
            raise ValueError("known obstacles must also be explored")


@dataclass(frozen=True, slots=True)
class ExplorationDelta:
    tick: int
    chunks: tuple[ExplorationChunk, ...]
    touched_keys: tuple[ChunkKey, ...] = ()

    def __post_init__(self) -> None:
        _validate_non_negative_integer("tick", self.tick)
        if not isinstance(self.chunks, tuple) or any(
            not isinstance(chunk, ExplorationChunk) for chunk in self.chunks
        ):
            raise TypeError("chunks must be a tuple of ExplorationChunk values")
        if not isinstance(self.touched_keys, tuple) or any(
            not isinstance(key, ChunkKey) for key in self.touched_keys
        ):
            raise TypeError("touched_keys must be a tuple of ChunkKey values")
        if tuple(sorted(set(self.touched_keys))) != self.touched_keys:
            raise ValueError("touched_keys must be unique and sorted")


@dataclass(frozen=True, slots=True)
class ExplorationWindow:
    revision: int
    explored_cells: tuple[Position, ...]
    known_obstacle_cells: tuple[Position, ...]

    def __post_init__(self) -> None:
        _validate_non_negative_integer("revision", self.revision)


def chunk_key(position: Position) -> ChunkKey:
    validate_position(position)
    return ChunkKey(position[0] // CHUNK_SIZE, position[1] // CHUNK_SIZE)


def bit_index(position: Position) -> int:
    key = chunk_key(position)
    local_x = position[0] - key.x * CHUNK_SIZE
    local_y = position[1] - key.y * CHUNK_SIZE
    return local_y * CHUNK_SIZE + local_x


def _empty_chunk(key: ChunkKey) -> ExplorationChunk:
    return ExplorationChunk(
        key=key,
        explored_mask=bytes(MASK_BYTES),
        obstacle_mask=bytes(MASK_BYTES),
        last_seen_tick=0,
        revision=0,
    )


def _mask_contains(mask: bytes, index: int) -> bool:
    byte_index, offset = divmod(index, 8)
    return bool(mask[byte_index] & (1 << offset))


def _set_mask_bit(mask: bytearray, index: int) -> None:
    byte_index, offset = divmod(index, 8)
    mask[byte_index] |= 1 << offset


def _mask_union(left: bytes, right: bytes) -> bytes:
    return bytes(a | b for a, b in zip(left, right, strict=True))


def _positions_from_mask(key: ChunkKey, mask: bytes) -> list[Position]:
    positions: list[Position] = []
    origin_x = key.x * CHUNK_SIZE
    origin_y = key.y * CHUNK_SIZE
    for index in range(CHUNK_SIZE * CHUNK_SIZE):
        if not _mask_contains(mask, index):
            continue
        positions.append(
            (origin_x + index % CHUNK_SIZE, origin_y + index // CHUNK_SIZE)
        )
    return positions


class ExplorationMap:
    """A bounded in-memory projection of persisted monotonic exploration."""

    def __init__(self) -> None:
        self._chunks: dict[ChunkKey, ExplorationChunk] = {}
        self._account_revision = 0

    @property
    def account_revision(self) -> int:
        return self._account_revision

    def merge_loaded(
        self,
        chunks: tuple[ExplorationChunk, ...],
        *,
        account_revision: int,
    ) -> None:
        _validate_non_negative_integer("account_revision", account_revision)
        if not isinstance(chunks, tuple):
            raise TypeError("chunks must be a tuple")
        for loaded in sorted(chunks, key=lambda item: item.key):
            if not isinstance(loaded, ExplorationChunk):
                raise TypeError("chunks must contain ExplorationChunk values")
            current = self._chunks.get(loaded.key)
            if current is None:
                self._chunks[loaded.key] = loaded
                continue
            self._chunks[loaded.key] = ExplorationChunk(
                key=loaded.key,
                explored_mask=_mask_union(current.explored_mask, loaded.explored_mask),
                obstacle_mask=_mask_union(current.obstacle_mask, loaded.obstacle_mask),
                last_seen_tick=max(current.last_seen_tick, loaded.last_seen_tick),
                revision=max(current.revision, loaded.revision),
            )
        self._account_revision = max(self._account_revision, account_revision)

    def observe(
        self,
        *,
        visible_cells: frozenset[Position],
        visible_obstacles: frozenset[Position],
        tick: int,
    ) -> ExplorationDelta:
        _validate_non_negative_integer("tick", tick)
        if not isinstance(visible_cells, frozenset) or not isinstance(
            visible_obstacles,
            frozenset,
        ):
            raise TypeError("visible cells and obstacles must be frozensets")

        observed_cells = visible_cells | visible_obstacles
        grouped_cells: dict[ChunkKey, list[Position]] = {}
        grouped_obstacles: dict[ChunkKey, list[Position]] = {}
        for position in observed_cells:
            validate_position(position)
            grouped_cells.setdefault(chunk_key(position), []).append(position)
        for position in visible_obstacles:
            validate_position(position)
            grouped_obstacles.setdefault(chunk_key(position), []).append(position)

        touched_keys = tuple(sorted(grouped_cells))
        changed: list[ExplorationChunk] = []
        for key in touched_keys:
            current = self._chunks.get(key, _empty_chunk(key))
            explored_mask = bytearray(current.explored_mask)
            obstacle_mask = bytearray(current.obstacle_mask)
            for position in grouped_cells[key]:
                _set_mask_bit(explored_mask, bit_index(position))
            for position in grouped_obstacles.get(key, ()):
                index = bit_index(position)
                _set_mask_bit(explored_mask, index)
                _set_mask_bit(obstacle_mask, index)

            mask_changed = (
                bytes(explored_mask) != current.explored_mask
                or bytes(obstacle_mask) != current.obstacle_mask
            )
            updated = ExplorationChunk(
                key=key,
                explored_mask=bytes(explored_mask),
                obstacle_mask=bytes(obstacle_mask),
                last_seen_tick=max(current.last_seen_tick, tick),
                revision=max(current.revision, self._account_revision),
            )
            self._chunks[key] = updated
            if mask_changed:
                changed.append(updated)

        return ExplorationDelta(
            tick=tick,
            chunks=tuple(changed),
            touched_keys=touched_keys,
        )

    def is_explored(self, position: Position) -> bool:
        key = chunk_key(position)
        chunk = self._chunks.get(key)
        return chunk is not None and _mask_contains(chunk.explored_mask, bit_index(position))

    def is_known_obstacle(self, position: Position) -> bool:
        key = chunk_key(position)
        chunk = self._chunks.get(key)
        return chunk is not None and _mask_contains(chunk.obstacle_mask, bit_index(position))

    def loaded_keys(self) -> frozenset[ChunkKey]:
        return frozenset(self._chunks)

    def known_obstacle_cells(self) -> tuple[Position, ...]:
        cells = [
            position
            for key, chunk in self._chunks.items()
            for position in _positions_from_mask(key, chunk.obstacle_mask)
        ]
        return tuple(sorted(cells))

    def last_seen_tick(self, position: Position) -> int:
        chunk = self._chunks.get(chunk_key(position))
        return 0 if chunk is None else chunk.last_seen_tick

    def evict_except(self, keep: frozenset[ChunkKey], *, max_chunks: int) -> None:
        if not isinstance(keep, frozenset) or any(
            not isinstance(key, ChunkKey) for key in keep
        ):
            raise TypeError("keep must be a frozenset of ChunkKey values")
        if not isinstance(max_chunks, int) or isinstance(max_chunks, bool) or max_chunks < 0:
            raise ValueError("max_chunks must be a non-negative integer")
        retained = keep & self.loaded_keys()
        if len(retained) > max_chunks:
            raise ValueError("max_chunks cannot evict required chunks")
        removable = sorted(
            (chunk for key, chunk in self._chunks.items() if key not in retained),
            key=lambda chunk: (chunk.last_seen_tick, chunk.key),
        )
        remove_count = max(0, len(self._chunks) - max_chunks)
        for chunk in removable[:remove_count]:
            del self._chunks[chunk.key]

    def window(
        self,
        *,
        min_x: int,
        min_y: int,
        max_x: int,
        max_y: int,
    ) -> ExplorationWindow:
        validate_position((min_x, min_y))
        validate_position((max_x, max_y))
        if min_x > max_x or min_y > max_y:
            raise ValueError("window bounds must be ordered")

        explored: list[Position] = []
        obstacles: list[Position] = []
        for key in sorted(self._chunks):
            chunk = self._chunks[key]
            for position in _positions_from_mask(key, chunk.explored_mask):
                if min_x <= position[0] <= max_x and min_y <= position[1] <= max_y:
                    explored.append(position)
            for position in _positions_from_mask(key, chunk.obstacle_mask):
                if min_x <= position[0] <= max_x and min_y <= position[1] <= max_y:
                    obstacles.append(position)
        return ExplorationWindow(
            revision=self._account_revision,
            explored_cells=tuple(sorted(explored)),
            known_obstacle_cells=tuple(sorted(obstacles)),
        )
