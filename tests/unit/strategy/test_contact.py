from __future__ import annotations

from app.strategy.contact import (
    ContactLevel,
    ContactMemory,
    assess_contact,
    choose_worker_evasion,
    ranger_intercept_goal,
    select_responder,
    update_investigation,
)
from app.strategy.models import EntityKind, EntitySnapshot


def entity(
    identifier: bytes,
    kind: EntityKind,
    position: tuple[int, int],
    *,
    hp: int = 2,
    controlled: bool = True,
) -> EntitySnapshot:
    return EntitySnapshot(identifier, kind, position, hp=hp, controlled=controlled)


def test_remote_visible_enemy_is_spotted_without_inflating_core_defense() -> None:
    core = entity(b"core", EntityKind.CORE, (0, 0), hp=5)
    worker = entity(b"worker", EntityKind.WORKER, (10, 0))
    enemy = entity(b"enemy", EntityKind.RANGER, (15, 0), controlled=False)

    assessment = assess_contact(
        core=core,
        friendlies=(worker,),
        visible_enemies=(enemy,),
        obstacles=frozenset(),
        protected_friendly_ids=frozenset({b"worker"}),
    )

    assert assessment.level is ContactLevel.SPOTTED
    assert assessment.threatened_friendly_ids == frozenset()


def test_enemy_that_can_hit_or_step_to_hit_a_worker_is_threatening() -> None:
    core = entity(b"core", EntityKind.CORE, (0, 0), hp=5)
    worker = entity(b"worker", EntityKind.WORKER, (10, 0))
    enemy = entity(b"enemy", EntityKind.RANGER, (14, 0), controlled=False)

    assessment = assess_contact(
        core=core,
        friendlies=(worker,),
        visible_enemies=(enemy,),
        obstacles=frozenset(),
        protected_friendly_ids=frozenset({b"worker"}),
    )

    assert assessment.level is ContactLevel.THREATENING
    assert assessment.threatened_friendly_ids == frozenset({b"worker"})
    assert assessment.threatening_enemy_ids == frozenset({b"enemy"})


def test_current_legal_combat_attack_is_engaged() -> None:
    core = entity(b"core", EntityKind.CORE, (0, 0), hp=5)
    ranger = entity(b"ranger", EntityKind.RANGER, (10, 0))
    enemy = entity(b"enemy", EntityKind.RANGER, (13, 0), controlled=False)

    assessment = assess_contact(
        core=core,
        friendlies=(ranger,),
        visible_enemies=(enemy,),
        obstacles=frozenset(),
        protected_friendly_ids=frozenset(),
    )

    assert assessment.level is ContactLevel.ENGAGED
    assert assessment.currently_engaged_enemy_ids == frozenset({b"enemy"})


def test_worker_evasion_prefers_fewer_attacks_then_more_enemy_distance() -> None:
    worker = entity(b"worker", EntityKind.WORKER, (10, 0))
    enemy = entity(b"enemy", EntityKind.RANGER, (13, 0), controlled=False)

    destination = choose_worker_evasion(
        worker,
        visible_enemies=(enemy,),
        obstacles=frozenset(),
        occupied=frozenset(),
        reserved=frozenset(),
        core_position=(0, 0),
    )

    assert destination == (9, 0)


def test_worker_evasion_escapes_a_one_step_ranger_threat() -> None:
    worker = entity(b"worker", EntityKind.WORKER, (0, 0))
    enemy = entity(b"enemy", EntityKind.RANGER, (0, 4), controlled=False)

    destination = choose_worker_evasion(
        worker,
        visible_enemies=(enemy,),
        obstacles=frozenset(),
        occupied=frozenset(),
        reserved=frozenset(),
        core_position=(10, 10),
    )

    assert destination in {(-1, 0), (1, 0)}


def test_ranger_is_responder_and_one_vanguard_remains_guard() -> None:
    ranger = entity(b"ranger", EntityKind.RANGER, (0, 3))
    guard = entity(b"guard", EntityKind.VANGUARD, (1, 0), hp=4)
    enemy = entity(b"enemy", EntityKind.RANGER, (12, 0), controlled=False)

    responder = select_responder(
        (guard, ranger),
        enemy=enemy,
        contact_level=ContactLevel.THREATENING,
        core_position=(0, 0),
        defender_ids=frozenset({b"guard"}),
        core_defense_level="CLEAR",
        obstacles=frozenset(),
    )

    assert responder is not None
    assert responder.entity_id == b"ranger"


def test_sole_selected_vanguard_guard_never_becomes_responder() -> None:
    guard = entity(b"guard", EntityKind.VANGUARD, (1, 0), hp=4)
    remote = entity(b"remote", EntityKind.VANGUARD, (20, 0), hp=4)
    enemy = entity(b"enemy", EntityKind.RANGER, (4, 0), controlled=False)

    responder = select_responder(
        (guard, remote),
        enemy=enemy,
        contact_level=ContactLevel.THREATENING,
        core_position=(0, 0),
        defender_ids=frozenset({b"guard"}),
        core_defense_level="CLEAR",
        obstacles=frozenset(),
    )

    assert responder is not None
    assert responder.entity_id == b"remote"


def test_ranger_intercept_goal_creates_a_legal_clear_shot_cell() -> None:
    ranger = entity(b"ranger", EntityKind.RANGER, (0, 0))
    enemy = entity(b"enemy", EntityKind.RANGER, (8, 0), controlled=False)

    goal = ranger_intercept_goal(
        ranger,
        enemy,
        obstacles=frozenset(),
        occupied=frozenset(),
        reserved=frozenset(),
        search_radius=8,
    )

    assert goal is not None
    assert max(
        abs(goal[0] - enemy.position[0]),
        abs(goal[1] - enemy.position[1]),
    ) <= 3


def test_contact_loss_creates_only_a_three_tick_movement_investigation() -> None:
    memory = ContactMemory()
    update_investigation(
        memory,
        tick=20,
        visible_threat=entity(
            b"enemy",
            EntityKind.RANGER,
            (8, 0),
            controlled=False,
        ),
        responder_id=b"ranger",
    )

    assert update_investigation(
        memory,
        tick=21,
        visible_threat=None,
        responder_id=b"ranger",
    ) == (8, 0)
    assert update_investigation(
        memory,
        tick=23,
        visible_threat=None,
        responder_id=b"ranger",
    ) == (8, 0)
    assert update_investigation(
        memory,
        tick=24,
        visible_threat=None,
        responder_id=b"ranger",
    ) is None
    assert memory.enemy_id is None


def test_visible_empty_last_seen_cell_ends_investigation_early() -> None:
    memory = ContactMemory()
    threat = entity(b"enemy", EntityKind.RANGER, (8, 0), controlled=False)
    update_investigation(
        memory,
        tick=20,
        visible_threat=threat,
        responder_id=b"ranger",
    )

    assert update_investigation(
        memory,
        tick=21,
        visible_threat=None,
        responder_id=b"ranger",
        current_visible_cells=frozenset({(8, 0)}),
    ) is None
    assert memory.last_seen_position is None
