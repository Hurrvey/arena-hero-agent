from app.strategy.models import EntityKind, EntitySnapshot
from app.strategy.visibility import compute_visible_cells, supercover_cells


def entity(kind: EntityKind, position: tuple[int, int], suffix: int = 1) -> EntitySnapshot:
    return EntitySnapshot(bytes([suffix]), kind, position, hp=1)


def test_each_friendly_kind_uses_its_v014_manhattan_radius() -> None:
    radii = {
        EntityKind.CORE: 5,
        EntityKind.WORKER: 3,
        EntityKind.VANGUARD: 4,
        EntityKind.RANGER: 5,
    }

    for kind, radius in radii.items():
        visible = compute_visible_cells((entity(kind, (0, 0)),), frozenset())
        assert (radius, 0) in visible
        assert (radius - 1, 1) in visible
        assert (radius + 1, 0) not in visible


def test_obstacle_cell_is_visible_but_the_cell_behind_it_is_not() -> None:
    visible = compute_visible_cells(
        (entity(EntityKind.CORE, (0, 0)),),
        frozenset({(2, 0)}),
    )

    assert (2, 0) in visible
    assert (3, 0) not in visible
    assert (2, 1) in visible


def test_corner_supercover_checks_both_touched_cells() -> None:
    assert supercover_cells((0, 0), (2, 2)) == (
        (1, 0),
        (0, 1),
        (1, 1),
        (2, 1),
        (1, 2),
        (2, 2),
    )

    left_blocked = compute_visible_cells(
        (entity(EntityKind.CORE, (0, 0)),),
        frozenset({(1, 0)}),
    )
    right_blocked = compute_visible_cells(
        (entity(EntityKind.CORE, (0, 0)),),
        frozenset({(0, 1)}),
    )
    assert (2, 2) not in left_blocked
    assert (2, 2) not in right_blocked


def test_union_contains_visibility_from_every_living_friendly() -> None:
    visible = compute_visible_cells(
        (
            entity(EntityKind.WORKER, (0, 0), 1),
            entity(EntityKind.VANGUARD, (10, 0), 2),
            EntitySnapshot(b"dead", EntityKind.RANGER, (20, 0), hp=0),
        ),
        frozenset(),
    )

    assert (-3, 0) in visible
    assert (14, 0) in visible
    assert (20, 0) not in visible
