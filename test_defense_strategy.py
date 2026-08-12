from dataclasses import dataclass

from defense_strategy import (
    ThreatLevel,
    assess_core_defense,
    select_defenders,
)


@dataclass(frozen=True)
class FakeUnit:
    id: int
    unit_type: str
    position: tuple[int, int]


def enemy(identifier, unit_type, position):
    return FakeUnit(identifier, unit_type, position)


def test_threat_levels_cover_clear_watch_approach_attack_and_lethal():
    core = (0, 0)

    clear = assess_core_defense(core, 5, 5, [], set(), watch_radius=5)
    watch = assess_core_defense(
        core,
        5,
        5,
        [enemy(1, "VANGUARD", (4, 0))],
        set(),
        watch_radius=5,
    )
    approach = assess_core_defense(
        core,
        5,
        5,
        [enemy(2, "VANGUARD", (2, 0))],
        set(),
        watch_radius=5,
    )
    attack = assess_core_defense(
        core,
        5,
        5,
        [enemy(3, "RANGER", (0, 3))],
        set(),
        watch_radius=5,
    )
    lethal = assess_core_defense(
        core,
        1,
        1,
        [
            enemy(4, "RANGER", (0, 3)),
            enemy(5, "VANGUARD", (1, 0)),
        ],
        set(),
        watch_radius=5,
    )

    assert clear.level is ThreatLevel.CLEAR
    assert watch.level is ThreatLevel.WATCH
    assert approach.level is ThreatLevel.APPROACH
    assert attack.level is ThreatLevel.ATTACK
    assert attack.attacker_ids == frozenset({3})
    assert lethal.level is ThreatLevel.LETHAL
    assert lethal.incoming_damage == 2
    assert lethal.core_effective_hp == 2


def test_ranger_attack_and_approach_are_obstacle_aware():
    blocked_shot = assess_core_defense(
        (0, 0),
        5,
        5,
        [enemy(1, "RANGER", (0, 3))],
        {(0, 2)},
        watch_radius=5,
    )
    blocked_approach = assess_core_defense(
        (0, 0),
        5,
        5,
        [enemy(2, "RANGER", (0, 4))],
        {(0, 2), (-1, 4), (1, 4), (0, 3), (0, 5)},
        watch_radius=5,
    )

    assert blocked_shot.level is ThreatLevel.WATCH
    assert blocked_shot.attacker_ids == frozenset()
    assert blocked_approach.level is ThreatLevel.WATCH


def test_noncombat_objects_do_not_create_core_threats():
    assessment = assess_core_defense(
        (0, 0),
        1,
        0,
        [
            enemy(1, "WORKER", (1, 0)),
            enemy(2, "CORE", (0, 1)),
        ],
        set(),
        watch_radius=5,
    )

    assert assessment.level is ThreatLevel.CLEAR
    assert assessment.attacker_ids == frozenset()


def test_select_defenders_is_deterministic_and_excludes_carrier():
    units = [
        enemy(40, "VANGUARD", (4, 0)),
        enemy(20, "VANGUARD", (2, 0)),
        enemy(10, "VANGUARD", (0, 2)),
        enemy(50, "RANGER", (0, 5)),
        enemy(30, "RANGER", (3, 0)),
        enemy(60, "RANGER", (0, 3)),
    ]

    roster = select_defenders(
        (0, 0),
        units,
        carrier_id=10,
        vanguard_target=1,
        ranger_target=2,
    )

    assert roster.vanguard_ids == frozenset({20})
    assert roster.ranger_ids == frozenset({30, 60})
    assert roster.all_ids == frozenset({20, 30, 60})
