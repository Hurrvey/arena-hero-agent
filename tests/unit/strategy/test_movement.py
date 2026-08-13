from app.strategy.models import CellRisk
from app.strategy.movement import MoveCandidate, MoveIntent, resolve_movement

OWNER = b"ours"
ENEMY = b"enemy"


def candidate(
    destination: tuple[int, int],
    direction: str,
    *,
    attacks: int = 0,
    goal_distance: int = 0,
    lethal: bool = False,
    reason: str = "TASK_PROGRESS",
) -> MoveCandidate:
    attackers = tuple(bytes([index + 1]) for index in range(attacks))
    return MoveCandidate(
        destination=destination,
        direction=direction,
        risk=CellRisk(attacks, attacks, attackers),
        goal_distance=goal_distance,
        reason_code=reason,
        lethal=lethal,
    )


def intent(
    entity_id: bytes,
    origin: tuple[int, int],
    *candidates: MoveCandidate,
    priority: int = 1,
) -> MoveIntent:
    return MoveIntent(entity_id, origin, priority, candidates)


def test_cannot_enter_a_friendly_waiting_occupants_cell() -> None:
    result = resolve_movement(
        (intent(b"a", (0, 0), candidate((1, 0), "RIGHT")),),
        occupancy={(0, 0): (b"a",), (1, 0): (b"b",)},
        owner_by_entity={b"a": OWNER, b"b": OWNER},
        obstacles=frozenset(),
        capacity=1,
    )

    assert result.accepted == {}
    assert result.rejected[-1].entity_id == b"a"


def test_dependency_chain_succeeds_when_every_occupant_leaves() -> None:
    result = resolve_movement(
        (
            intent(b"a", (0, 0), candidate((1, 0), "RIGHT"), priority=10),
            intent(b"b", (1, 0), candidate((2, 0), "RIGHT"), priority=1),
        ),
        occupancy={(0, 0): (b"a",), (1, 0): (b"b",)},
        owner_by_entity={b"a": OWNER, b"b": OWNER},
        obstacles=frozenset(),
        capacity=1,
    )

    assert {entity_id: move.destination for entity_id, move in result.accepted.items()} == {
        b"a": (1, 0),
        b"b": (2, 0),
    }
    assert [(edge.entity_id, edge.depends_on) for edge in result.dependency_edges] == [(b"a", b"b")]


def test_dependency_chain_falls_back_when_the_tail_cannot_leave() -> None:
    result = resolve_movement(
        (
            intent(
                b"a",
                (0, 0),
                candidate((1, 0), "RIGHT"),
                candidate((0, -1), "UP"),
                priority=10,
            ),
            intent(b"b", (1, 0), candidate((2, 0), "RIGHT"), priority=1),
        ),
        occupancy={(0, 0): (b"a",), (1, 0): (b"b",), (2, 0): (b"c",)},
        owner_by_entity={b"a": OWNER, b"b": OWNER, b"c": OWNER},
        obstacles=frozenset(),
        capacity=1,
    )

    assert result.accepted[b"a"].destination == (0, -1)
    assert b"b" not in result.accepted


def test_same_destination_uses_priority_then_raw_uuid() -> None:
    intents = (
        intent(b"z", (0, 0), candidate((1, 0), "RIGHT"), priority=5),
        intent(b"b", (2, 0), candidate((1, 0), "LEFT"), priority=10),
        intent(b"a", (1, 1), candidate((1, 0), "UP"), priority=10),
    )
    result = resolve_movement(
        intents,
        occupancy={(0, 0): (b"z",), (2, 0): (b"b",), (1, 1): (b"a",)},
        owner_by_entity={b"a": OWNER, b"b": OWNER, b"z": OWNER},
        obstacles=frozenset(),
        capacity=1,
    )

    assert tuple(result.accepted) == (b"a",)


def test_incomplete_cycle_rejects_all_dependants() -> None:
    result = resolve_movement(
        (
            intent(b"a", (0, 0), candidate((1, 0), "RIGHT"), priority=2),
            intent(b"b", (1, 0), candidate((2, 0), "RIGHT"), priority=1),
        ),
        occupancy={(0, 0): (b"a",), (1, 0): (b"b",), (2, 0): (b"c",)},
        owner_by_entity={b"a": OWNER, b"b": OWNER, b"c": OWNER},
        obstacles=frozenset(),
        capacity=1,
    )

    assert result.accepted == {}


def test_legal_four_cell_cycle_is_accepted_atomically() -> None:
    result = resolve_movement(
        (
            intent(b"a", (0, 0), candidate((1, 0), "RIGHT")),
            intent(b"b", (1, 0), candidate((1, 1), "DOWN")),
            intent(b"c", (1, 1), candidate((0, 1), "LEFT")),
            intent(b"d", (0, 1), candidate((0, 0), "UP")),
        ),
        occupancy={
            (0, 0): (b"a",),
            (1, 0): (b"b",),
            (1, 1): (b"c",),
            (0, 1): (b"d",),
        },
        owner_by_entity={key: OWNER for key in (b"a", b"b", b"c", b"d")},
        obstacles=frozenset(),
        capacity=1,
    )

    assert set(result.accepted) == {b"a", b"b", b"c", b"d"}
    assert len(result.dependency_edges) == 4


def test_enemy_occupied_and_obstacle_cells_are_rejected() -> None:
    result = resolve_movement(
        (
            intent(
                b"a",
                (0, 0),
                candidate((1, 0), "RIGHT"),
                candidate((-1, 0), "LEFT"),
                candidate((0, -1), "UP"),
            ),
        ),
        occupancy={(0, 0): (b"a",), (-1, 0): (b"enemy",)},
        owner_by_entity={b"a": OWNER, b"enemy": ENEMY},
        obstacles=frozenset({(1, 0)}),
    )

    assert result.accepted[b"a"].destination == (0, -1)


def test_zero_risk_candidate_beats_a_closer_attacked_candidate() -> None:
    result = resolve_movement(
        (
            intent(
                b"a",
                (0, 0),
                candidate((1, 0), "RIGHT", attacks=1, goal_distance=0),
                candidate((0, -1), "UP", attacks=0, goal_distance=4),
            ),
        ),
        occupancy={(0, 0): (b"a",)},
        owner_by_entity={b"a": OWNER},
        obstacles=frozenset(),
    )

    assert result.accepted[b"a"].destination == (0, -1)


def test_when_all_cells_are_risky_the_lowest_damage_candidate_wins() -> None:
    result = resolve_movement(
        (
            intent(
                b"a",
                (0, 0),
                candidate((1, 0), "RIGHT", attacks=2, goal_distance=0),
                candidate((0, -1), "UP", attacks=1, goal_distance=3),
            ),
        ),
        occupancy={(0, 0): (b"a",)},
        owner_by_entity={b"a": OWNER},
        obstacles=frozenset(),
    )

    assert result.accepted[b"a"].destination == (0, -1)


def test_lethal_candidate_is_rejected_without_explicit_sacrifice_reason() -> None:
    rejected = resolve_movement(
        (
            intent(
                b"a",
                (0, 0),
                candidate((1, 0), "RIGHT", lethal=True),
            ),
        ),
        occupancy={(0, 0): (b"a",)},
        owner_by_entity={b"a": OWNER},
        obstacles=frozenset(),
    )
    sacrifice = resolve_movement(
        (
            intent(
                b"a",
                (0, 0),
                candidate(
                    (1, 0),
                    "RIGHT",
                    lethal=True,
                    reason="CORE_DEFENSE_SACRIFICE",
                ),
            ),
        ),
        occupancy={(0, 0): (b"a",)},
        owner_by_entity={b"a": OWNER},
        obstacles=frozenset(),
    )

    assert rejected.accepted == {}
    assert sacrifice.accepted[b"a"].destination == (1, 0)


def test_resolution_is_independent_of_mapping_and_intent_insertion_order() -> None:
    intents = (
        intent(b"a", (0, 0), candidate((1, 0), "RIGHT"), priority=2),
        intent(b"b", (1, 0), candidate((2, 0), "RIGHT"), priority=1),
    )
    forward = resolve_movement(
        intents,
        occupancy={(0, 0): (b"a",), (1, 0): (b"b",)},
        owner_by_entity={b"a": OWNER, b"b": OWNER},
        obstacles=frozenset(),
        capacity=1,
    )
    reverse = resolve_movement(
        tuple(reversed(intents)),
        occupancy={(1, 0): (b"b",), (0, 0): (b"a",)},
        owner_by_entity={b"b": OWNER, b"a": OWNER},
        obstacles=frozenset(),
        capacity=1,
    )

    assert forward == reverse
