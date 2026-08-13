"""Deterministic, capacity-aware global resolution of friendly movement intents."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from types import MappingProxyType

from .models import CellRisk, Position, validate_position

_DIRECTION_DELTAS: dict[str, Position] = {
    "UP": (0, -1),
    "RIGHT": (1, 0),
    "DOWN": (0, 1),
    "LEFT": (-1, 0),
}
_DIRECTION_ORDER = {direction: index for index, direction in enumerate(_DIRECTION_DELTAS)}
_SACRIFICE_REASON = "CORE_DEFENSE_SACRIFICE"


@dataclass(frozen=True, slots=True)
class MoveCandidate:
    destination: Position
    direction: str
    risk: CellRisk
    goal_distance: int
    reason_code: str
    lethal: bool = False
    stagnation_penalty: int = 0
    oscillation_penalty: int = 0

    def __post_init__(self) -> None:
        validate_position(self.destination)
        if self.direction not in _DIRECTION_DELTAS:
            raise ValueError("direction must be UP, RIGHT, DOWN, or LEFT")
        if not isinstance(self.risk, CellRisk):
            raise TypeError("risk must be a CellRisk")
        for name in ("goal_distance", "stagnation_penalty", "oscillation_penalty"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if not isinstance(self.reason_code, str) or not self.reason_code.strip():
            raise ValueError("reason_code must be a non-empty string")
        if not isinstance(self.lethal, bool):
            raise TypeError("lethal must be a boolean")


@dataclass(frozen=True, slots=True)
class MoveIntent:
    entity_id: bytes
    origin: Position
    priority: int
    candidates: tuple[MoveCandidate, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.entity_id, bytes) or not self.entity_id:
            raise TypeError("entity_id must be non-empty bytes")
        validate_position(self.origin)
        if not isinstance(self.priority, int) or isinstance(self.priority, bool):
            raise TypeError("priority must be an integer")
        if not isinstance(self.candidates, tuple) or any(
            not isinstance(candidate, MoveCandidate) for candidate in self.candidates
        ):
            raise TypeError("candidates must be a tuple of MoveCandidate values")


@dataclass(frozen=True, slots=True)
class MovementDependency:
    entity_id: bytes
    depends_on: bytes
    destination: Position


@dataclass(frozen=True, slots=True)
class RejectedMove:
    entity_id: bytes
    candidate: MoveCandidate
    reason: str


@dataclass(frozen=True, slots=True)
class MovementResolution:
    accepted: Mapping[bytes, MoveCandidate]
    rejected: tuple[RejectedMove, ...]
    dependency_edges: tuple[MovementDependency, ...]


def _candidate_rank(
    candidate: MoveCandidate,
    occupancy: Mapping[Position, tuple[bytes, ...]],
    capacity: int,
) -> tuple[int, int, int, int, int, int, int, int, Position, str]:
    dependency_penalty = int(len(occupancy.get(candidate.destination, ())) >= capacity)
    return (
        int(candidate.lethal),
        candidate.risk.visible_attack_count,
        candidate.risk.expected_damage,
        dependency_penalty,
        candidate.stagnation_penalty,
        candidate.oscillation_penalty,
        candidate.goal_distance,
        _DIRECTION_ORDER[candidate.direction],
        candidate.destination,
        candidate.reason_code,
    )


def _static_rejection_reason(
    intent: MoveIntent,
    candidate: MoveCandidate,
    *,
    occupancy: Mapping[Position, tuple[bytes, ...]],
    owner_by_entity: Mapping[bytes, bytes],
    obstacles: AbstractSet[Position],
) -> str | None:
    step_x, step_y = _DIRECTION_DELTAS[candidate.direction]
    if candidate.destination != (intent.origin[0] + step_x, intent.origin[1] + step_y):
        return "INVALID_DIRECTION_OR_DESTINATION"
    if candidate.destination in obstacles:
        return "OBSTACLE"
    if candidate.lethal and candidate.reason_code != _SACRIFICE_REASON:
        return "LETHAL_WITHOUT_SACRIFICE"

    mover_owner = owner_by_entity[intent.entity_id]
    if any(
        owner_by_entity[occupant] != mover_owner
        for occupant in occupancy.get(candidate.destination, ())
    ):
        return "VISIBLE_ENEMY_OCCUPIED"
    return None


def _dependency_edges(
    selected: Mapping[bytes, MoveCandidate],
    intents: Mapping[bytes, MoveIntent],
    occupancy: Mapping[Position, tuple[bytes, ...]],
    capacity: int,
) -> tuple[MovementDependency, ...]:
    edges: list[MovementDependency] = []
    entrants: dict[Position, list[bytes]] = defaultdict(list)
    for entity_id, candidate in selected.items():
        entrants[candidate.destination].append(entity_id)

    for destination in sorted(entrants):
        current = occupancy.get(destination, ())
        if len(current) + len(entrants[destination]) <= capacity:
            continue
        departing = tuple(
            sorted(
                occupant
                for occupant in current
                if occupant in selected and intents[occupant].origin == destination
            )
        )
        for entrant in sorted(entrants[destination]):
            for occupant in departing:
                edges.append(MovementDependency(entrant, occupant, destination))
    return tuple(edges)


def resolve_movement(
    intents: Sequence[MoveIntent],
    *,
    occupancy: Mapping[Position, tuple[bytes, ...]],
    owner_by_entity: Mapping[bytes, bytes],
    obstacles: AbstractSet[Position],
    capacity: int = 2,
) -> MovementResolution:
    """Resolve friendly moves as a deterministic final-occupancy transaction.

    Invalid candidates and losing destination contenders advance to their next
    candidate. A selected move leaves its origin before final capacity is
    checked, so complete dependency chains and legal cycles resolve atomically.
    """

    if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity < 1:
        raise ValueError("capacity must be a positive integer")

    intent_by_id: dict[bytes, MoveIntent] = {}
    for item in intents:
        if item.entity_id in intent_by_id:
            raise ValueError("movement intents must have unique entity identifiers")
        intent_by_id[item.entity_id] = item

    for position, occupants in occupancy.items():
        validate_position(position)
        if len(set(occupants)) != len(occupants):
            raise ValueError("occupancy cannot contain a duplicate entity")
        for occupant in occupants:
            if occupant not in owner_by_entity:
                raise ValueError("every occupying entity must have an owner")
    for entity_id, item in intent_by_id.items():
        if entity_id not in owner_by_entity:
            raise ValueError("every moving entity must have an owner")
        if entity_id not in occupancy.get(item.origin, ()):
            raise ValueError("every moving entity must occupy its declared origin")
    for obstacle in obstacles:
        validate_position(obstacle)

    rejected: list[RejectedMove] = []
    options: dict[bytes, tuple[MoveCandidate, ...]] = {}
    for entity_id in sorted(intent_by_id):
        item = intent_by_id[entity_id]
        legal: list[MoveCandidate] = []
        for candidate in item.candidates:
            reason = _static_rejection_reason(
                item,
                candidate,
                occupancy=occupancy,
                owner_by_entity=owner_by_entity,
                obstacles=obstacles,
            )
            if reason is None:
                legal.append(candidate)
            else:
                rejected.append(RejectedMove(entity_id, candidate, reason))
        options[entity_id] = tuple(
            sorted(legal, key=lambda move: _candidate_rank(move, occupancy, capacity))
        )

    next_option = {entity_id: 0 for entity_id in intent_by_id}
    while True:
        selected = {
            entity_id: options[entity_id][index]
            for entity_id, index in next_option.items()
            if index < len(options[entity_id])
        }
        if not selected:
            break

        stationary: dict[Position, list[bytes]] = {
            cell: [
                occupant
                for occupant in occupants
                if occupant not in selected
                or intent_by_id.get(occupant, MoveIntent(occupant, cell, 0, ())).origin != cell
            ]
            for cell, occupants in occupancy.items()
        }
        entrants: dict[Position, list[bytes]] = defaultdict(list)
        for entity_id, candidate in selected.items():
            entrants[candidate.destination].append(entity_id)

        losers: dict[bytes, str] = {}
        for destination in sorted(entrants):
            contenders = sorted(
                entrants[destination],
                key=lambda entity_id: (-intent_by_id[entity_id].priority, entity_id),
            )
            available = max(0, capacity - len(stationary.get(destination, ())))
            for entity_id in contenders[available:]:
                losers[entity_id] = "CELL_CAPACITY_OR_DESTINATION_CONFLICT"

        if not losers:
            accepted = MappingProxyType(dict(sorted(selected.items())))
            return MovementResolution(
                accepted=accepted,
                rejected=tuple(rejected),
                dependency_edges=_dependency_edges(
                    accepted,
                    intent_by_id,
                    occupancy,
                    capacity,
                ),
            )

        for entity_id in sorted(losers):
            candidate = selected[entity_id]
            rejected.append(RejectedMove(entity_id, candidate, losers[entity_id]))
            next_option[entity_id] += 1

    return MovementResolution(MappingProxyType({}), tuple(rejected), ())
