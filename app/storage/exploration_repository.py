"""Account-scoped SQLite persistence for sparse exploration chunks."""

from __future__ import annotations

from collections.abc import Iterable

from app.strategy.exploration import (
    MASK_BYTES,
    ChunkKey,
    ExplorationChunk,
    ExplorationDelta,
    ExplorationMap,
    ExplorationWindow,
)

from .database import Database, utc_now

_MAX_LOAD_KEYS = 64
_MAX_WINDOW_AREA = 96 * 96


def _validate_scope(account_scope: str) -> None:
    if not isinstance(account_scope, str) or not account_scope:
        raise ValueError("account_scope must be a non-empty string")


def _validate_integer(name: str, value: int, *, minimum: int = 0) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValueError(f"{name} must be an integer of at least {minimum}")


def _mask_union(left: bytes, right: bytes) -> bytes:
    if len(left) != MASK_BYTES or len(right) != MASK_BYTES:
        raise ValueError("exploration masks must contain exactly 128 bytes")
    return bytes(a | b for a, b in zip(left, right, strict=True))


def _validate_obstacle_subset(explored_mask: bytes, obstacle_mask: bytes) -> None:
    if any(
        obstacle & ~explored
        for obstacle, explored in zip(obstacle_mask, explored_mask, strict=True)
    ):
        raise ValueError("known obstacles must also be explored")


class ExplorationRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def revision(self, account_scope: str) -> int:
        _validate_scope(account_scope)
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT revision FROM exploration_accounts WHERE account_scope = ?",
                (account_scope,),
            ).fetchone()
        return 0 if row is None else int(row[0])

    def load_chunks(
        self,
        account_scope: str,
        keys: tuple[ChunkKey, ...],
        *,
        busy_timeout_ms: int = 0,
    ) -> tuple[int, tuple[ExplorationChunk, ...]]:
        _validate_scope(account_scope)
        _validate_integer("busy_timeout_ms", busy_timeout_ms)
        if not isinstance(keys, tuple) or any(not isinstance(key, ChunkKey) for key in keys):
            raise TypeError("keys must be a tuple of ChunkKey values")
        normalized = tuple(sorted(set(keys)))
        if len(normalized) > _MAX_LOAD_KEYS:
            raise ValueError("at most 64 unique exploration chunks may be loaded")

        with self.database.connect() as connection:
            connection.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
            revision_row = connection.execute(
                "SELECT revision FROM exploration_accounts WHERE account_scope = ?",
                (account_scope,),
            ).fetchone()
            account_revision = 0 if revision_row is None else int(revision_row[0])
            if not normalized:
                return account_revision, ()
            predicate = " OR ".join(
                "(chunk_x = ? AND chunk_y = ?)" for _key in normalized
            )
            parameters: list[object] = [account_scope]
            for key in normalized:
                parameters.extend((key.x, key.y))
            rows = connection.execute(
                "SELECT chunk_x, chunk_y, explored_mask, obstacle_mask, "
                "last_seen_tick, revision FROM exploration_chunks "
                f"WHERE account_scope = ? AND ({predicate}) "
                "ORDER BY chunk_x, chunk_y",
                tuple(parameters),
            ).fetchall()

        chunks: list[ExplorationChunk] = []
        for row in rows:
            try:
                chunks.append(
                    ExplorationChunk(
                        key=ChunkKey(int(row[0]), int(row[1])),
                        explored_mask=bytes(row[2]),
                        obstacle_mask=bytes(row[3]),
                        last_seen_tick=int(row[4]),
                        revision=int(row[5]),
                    )
                )
            except (TypeError, ValueError):
                continue
        return account_revision, tuple(chunks)

    def merge_delta(self, account_scope: str, delta: ExplorationDelta) -> int:
        _validate_scope(account_scope)
        if not isinstance(delta, ExplorationDelta):
            raise TypeError("delta must be an ExplorationDelta")
        incoming = self._coalesce_chunks(delta.chunks)
        addressed = tuple(sorted(set(incoming) | set(delta.touched_keys)))
        now = utc_now()

        with self.database.connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "INSERT INTO exploration_accounts(account_scope, revision, updated_at) "
                    "VALUES (?, 0, ?) ON CONFLICT(account_scope) DO NOTHING",
                    (account_scope, now),
                )
                revision_row = connection.execute(
                    "SELECT revision FROM exploration_accounts WHERE account_scope = ?",
                    (account_scope,),
                ).fetchone()
                current_revision = int(revision_row[0])
                existing = self._load_addressed(connection, account_scope, addressed)

                merged: dict[ChunkKey, tuple[bytes, bytes, int]] = {}
                any_mask_changed = False
                for key, chunk in incoming.items():
                    prior = existing.get(key)
                    old_explored = bytes(MASK_BYTES) if prior is None else prior[0]
                    old_obstacle = bytes(MASK_BYTES) if prior is None else prior[1]
                    explored_mask = _mask_union(old_explored, chunk.explored_mask)
                    obstacle_mask = _mask_union(old_obstacle, chunk.obstacle_mask)
                    _validate_obstacle_subset(explored_mask, obstacle_mask)
                    last_seen_tick = max(
                        delta.tick,
                        chunk.last_seen_tick,
                        0 if prior is None else prior[2],
                    )
                    merged[key] = (explored_mask, obstacle_mask, last_seen_tick)
                    if explored_mask != old_explored or obstacle_mask != old_obstacle:
                        any_mask_changed = True

                next_revision = current_revision + int(any_mask_changed)
                for key, (explored_mask, obstacle_mask, last_seen_tick) in merged.items():
                    prior = existing.get(key)
                    changed = (
                        prior is None
                        or explored_mask != prior[0]
                        or obstacle_mask != prior[1]
                    )
                    row_revision = next_revision if changed else int(prior[3])
                    connection.execute(
                        "INSERT INTO exploration_chunks("
                        "account_scope, chunk_x, chunk_y, explored_mask, obstacle_mask, "
                        "last_seen_tick, revision, updated_at"
                        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT(account_scope, chunk_x, chunk_y) DO UPDATE SET "
                        "explored_mask = excluded.explored_mask, "
                        "obstacle_mask = excluded.obstacle_mask, "
                        "last_seen_tick = MAX(exploration_chunks.last_seen_tick, "
                        "excluded.last_seen_tick), revision = excluded.revision, "
                        "updated_at = excluded.updated_at",
                        (
                            account_scope,
                            key.x,
                            key.y,
                            explored_mask,
                            obstacle_mask,
                            last_seen_tick,
                            row_revision,
                            now,
                        ),
                    )

                for key in delta.touched_keys:
                    if key in merged:
                        continue
                    connection.execute(
                        "UPDATE exploration_chunks SET "
                        "last_seen_tick = MAX(last_seen_tick, ?), updated_at = ? "
                        "WHERE account_scope = ? AND chunk_x = ? AND chunk_y = ?",
                        (delta.tick, now, account_scope, key.x, key.y),
                    )

                if any_mask_changed:
                    connection.execute(
                        "UPDATE exploration_accounts SET revision = ?, updated_at = ? "
                        "WHERE account_scope = ?",
                        (next_revision, now, account_scope),
                    )
                connection.commit()
                return next_revision
            except Exception:
                connection.rollback()
                raise

    def window(
        self,
        account_scope: str,
        *,
        min_x: int,
        min_y: int,
        max_x: int,
        max_y: int,
    ) -> ExplorationWindow:
        _validate_scope(account_scope)
        for name, value in (
            ("min_x", min_x),
            ("min_y", min_y),
            ("max_x", max_x),
            ("max_y", max_y),
        ):
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"{name} must be an integer")
        if min_x > max_x or min_y > max_y:
            raise ValueError("exploration window bounds must be ordered")
        area = (max_x - min_x + 1) * (max_y - min_y + 1)
        if area > _MAX_WINDOW_AREA:
            raise ValueError("exploration window area must not exceed 9216 cells")

        keys = tuple(
            ChunkKey(chunk_x, chunk_y)
            for chunk_x in range(min_x // 32, max_x // 32 + 1)
            for chunk_y in range(min_y // 32, max_y // 32 + 1)
        )
        account_revision, chunks = self.load_chunks(account_scope, keys)
        exploration = ExplorationMap()
        exploration.merge_loaded(chunks, account_revision=account_revision)
        return exploration.window(
            min_x=min_x,
            min_y=min_y,
            max_x=max_x,
            max_y=max_y,
        )

    @staticmethod
    def _coalesce_chunks(
        chunks: Iterable[ExplorationChunk],
    ) -> dict[ChunkKey, ExplorationChunk]:
        merged: dict[ChunkKey, ExplorationChunk] = {}
        for chunk in chunks:
            prior = merged.get(chunk.key)
            if prior is None:
                merged[chunk.key] = chunk
                continue
            merged[chunk.key] = ExplorationChunk(
                key=chunk.key,
                explored_mask=_mask_union(prior.explored_mask, chunk.explored_mask),
                obstacle_mask=_mask_union(prior.obstacle_mask, chunk.obstacle_mask),
                last_seen_tick=max(prior.last_seen_tick, chunk.last_seen_tick),
                revision=max(prior.revision, chunk.revision),
            )
        return merged

    @staticmethod
    def _load_addressed(connection, account_scope: str, keys: tuple[ChunkKey, ...]):
        existing: dict[ChunkKey, tuple[bytes, bytes, int, int]] = {}
        for key in keys:
            row = connection.execute(
                "SELECT explored_mask, obstacle_mask, last_seen_tick, revision "
                "FROM exploration_chunks WHERE account_scope = ? "
                "AND chunk_x = ? AND chunk_y = ?",
                (account_scope, key.x, key.y),
            ).fetchone()
            if row is None:
                continue
            explored_mask = bytes(row[0])
            obstacle_mask = bytes(row[1])
            _validate_obstacle_subset(explored_mask, obstacle_mask)
            existing[key] = (
                explored_mask,
                obstacle_mask,
                int(row[2]),
                int(row[3]),
            )
        return existing
