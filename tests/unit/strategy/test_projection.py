from app.strategy.models import CellRisk, EntityKind, EntitySnapshot
from app.strategy.projection import compute_capacity_projection, should_defer_deposit


def unit(entity_id: bytes, position: tuple[int, int], hp: int = 2) -> EntitySnapshot:
    return EntitySnapshot(entity_id, EntityKind.WORKER, position, hp=hp)


def test_visible_doomed_unit_lowers_projected_capacity() -> None:
    units = tuple(unit(bytes([index]), (index, 0)) for index in range(1, 4))
    risk = {(1, 0): CellRisk(2, 2, (b"x", b"y"))}

    projection = compute_capacity_projection(
        units,
        risk_map=risk,
        planned_destinations={},
        current_resources=10,
        pending_deposit=5,
    )

    assert projection.current_population == 3
    assert projection.projected_population_floor == 2
    assert projection.current_capacity == 15
    assert projection.projected_capacity == 10
    assert projection.projected_overflow == 5
    assert projection.visibly_doomed_unit_ids == (b"\x01",)


def test_safe_planned_move_prevents_false_death_projection() -> None:
    projection = compute_capacity_projection(
        (unit(b"a", (0, 0), hp=1),),
        risk_map={(0, 0): CellRisk(1, 1, (b"enemy",))},
        planned_destinations={b"a": (0, 1)},
        current_resources=9,
        pending_deposit=1,
    )

    assert projection.visibly_doomed_unit_ids == ()
    assert projection.projected_capacity == 10
    assert projection.projected_overflow == 0


def test_deposit_is_deferred_when_post_combat_capacity_would_overflow() -> None:
    projection = compute_capacity_projection(
        tuple(unit(bytes([index]), (index, 0), hp=1) for index in range(1, 4)),
        risk_map={(1, 0): CellRisk(1, 1, (b"enemy",))},
        planned_destinations={},
        current_resources=10,
        pending_deposit=3,
    )

    assert should_defer_deposit(projection)
