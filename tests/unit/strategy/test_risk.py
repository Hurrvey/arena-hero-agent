from app.strategy.models import EntityKind, EntitySnapshot
from app.strategy.risk import build_visible_risk_map, risk_at


def enemy(
    kind: EntityKind,
    position: tuple[int, int],
    entity_id: bytes,
) -> EntitySnapshot:
    return EntitySnapshot(entity_id, kind, position, hp=2, controlled=False)


def test_vanguard_threatens_only_four_adjacent_cells() -> None:
    risk_map = build_visible_risk_map(
        (),
        (enemy(EntityKind.VANGUARD, (0, 0), b"v"),),
        frozenset(),
    )

    assert risk_at(risk_map, (1, 0)).expected_damage == 1
    assert risk_at(risk_map, (-1, 0)).expected_damage == 1
    assert risk_at(risk_map, (0, 1)).expected_damage == 1
    assert risk_at(risk_map, (0, -1)).expected_damage == 1
    assert risk_at(risk_map, (1, 1)).expected_damage == 0
    assert risk_at(risk_map, (2, 0)).expected_damage == 0


def test_ranger_threatens_row_column_and_exact_diagonal_at_range_three() -> None:
    risk_map = build_visible_risk_map(
        (),
        (enemy(EntityKind.RANGER, (0, 0), b"r"),),
        frozenset(),
    )

    for cell in ((3, 0), (-3, 0), (0, 3), (0, -3), (3, 3), (-3, -3)):
        assert risk_at(risk_map, cell).expected_damage == 1
    assert risk_at(risk_map, (4, 0)).expected_damage == 0


def test_ranger_does_not_threaten_a_two_by_one_offset() -> None:
    risk_map = build_visible_risk_map(
        (),
        (enemy(EntityKind.RANGER, (0, 0), b"r"),),
        frozenset(),
    )

    assert risk_at(risk_map, (2, 1)).expected_damage == 0


def test_obstacle_stops_ranger_risk_beyond_it() -> None:
    risk_map = build_visible_risk_map(
        (),
        (enemy(EntityKind.RANGER, (0, 0), b"r"),),
        frozenset({(1, 0)}),
    )

    assert risk_at(risk_map, (1, 0)).expected_damage == 1
    assert risk_at(risk_map, (2, 0)).expected_damage == 0
    assert risk_at(risk_map, (3, 0)).expected_damage == 0


def test_risk_map_contains_attack_count_damage_and_stable_attacker_ids() -> None:
    risk_map = build_visible_risk_map(
        (),
        (
            enemy(EntityKind.RANGER, (0, 3), b"z"),
            enemy(EntityKind.VANGUARD, (1, 0), b"a"),
            enemy(EntityKind.WORKER, (0, 1), b"worker"),
        ),
        frozenset(),
    )

    risk = risk_at(risk_map, (0, 0))
    assert risk.visible_attack_count == 2
    assert risk.expected_damage == 2
    assert risk.attackers == (b"a", b"z")
