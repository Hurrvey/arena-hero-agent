from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import pytest

from app.strategy.models import EntityKind, entity_snapshot_from_view


@pytest.mark.parametrize(
    ("view", "controlled", "expected_kind", "expected_shield"),
    [
        (
            SimpleNamespace(
                id=UUID(int=1),
                kind="CORE",
                position=(0, 0),
                hp=5,
                shield=4,
            ),
            True,
            EntityKind.CORE,
            4,
        ),
        (
            SimpleNamespace(
                id=UUID(int=2),
                kind="UNIT",
                unit_type="WORKER",
                position=(1, 0),
                hp=2,
            ),
            True,
            EntityKind.WORKER,
            0,
        ),
        (
            SimpleNamespace(
                id=UUID(int=3),
                unit_type="VANGUARD",
                position=(2, 0),
                hp=4,
            ),
            False,
            EntityKind.VANGUARD,
            0,
        ),
        (
            SimpleNamespace(
                id=UUID(int=4),
                unit_type="RANGER",
                position=(3, 0),
                hp=2,
            ),
            True,
            EntityKind.RANGER,
            0,
        ),
    ],
)
def test_entity_snapshot_projects_supported_living_views(
    view,
    controlled,
    expected_kind,
    expected_shield,
) -> None:
    snapshot = entity_snapshot_from_view(view, controlled=controlled)

    assert snapshot is not None
    assert snapshot.entity_id == view.id.bytes
    assert snapshot.kind is expected_kind
    assert snapshot.position == tuple(view.position)
    assert snapshot.shield == expected_shield
    assert snapshot.controlled is controlled


def test_entity_snapshot_ignores_unknown_and_dead_units() -> None:
    unknown = SimpleNamespace(
        id=UUID(int=5),
        unit_type="FUTURE_UNIT",
        position=(0, 0),
        hp=2,
    )
    dead = SimpleNamespace(
        id=UUID(int=6),
        unit_type="WORKER",
        position=(0, 0),
        hp=0,
    )

    assert entity_snapshot_from_view(unknown, controlled=True) is None
    assert entity_snapshot_from_view(dead, controlled=True) is None


def test_entity_snapshot_has_deterministic_non_uuid_identifier_bytes() -> None:
    view = SimpleNamespace(
        id="worker-z",
        unit_type="WORKER",
        position=[-2, 7],
        hp=2,
    )

    snapshot = entity_snapshot_from_view(view, controlled=True)

    assert snapshot is not None
    assert snapshot.entity_id == b"worker-z"
    assert snapshot.position == (-2, 7)
