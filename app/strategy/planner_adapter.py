"""Adapter from the existing authoritative tactic to a UI-safe PlannerResult."""

from __future__ import annotations

from collections.abc import Callable
from types import MappingProxyType
from typing import Any

from arena_hero import Direction

from .planner import DecisionAction, DecisionExplanation, PlannerDiagnostics, PlannerResult


def _raw_id(identifier: object) -> bytes:
    raw = getattr(identifier, "bytes", None)
    if isinstance(raw, bytes):
        return raw
    if isinstance(identifier, bytes):
        return identifier
    return str(identifier).encode("utf-8", "replace")


def _action_type(action: object) -> str:
    value = getattr(action, "type", None)
    return str(getattr(value, "value", value) or "WAIT").upper()


def _target(action: object, origin: tuple[int, int] | None) -> tuple[int, int] | None:
    expected = getattr(action, "expected_cell", None)
    if expected is not None:
        return tuple(expected)
    direction = getattr(action, "direction", None)
    delta = getattr(direction, "delta", None)
    if origin is not None and delta is not None:
        return (origin[0] + delta[0], origin[1] + delta[1])
    return None


def _reason_for(action_type: str) -> str:
    return {
        "HARVEST": "CURRENT_RESOURCE_HARVEST",
        "DEPOSIT": "CORE_RESOURCE_DEPOSIT",
        "MOVE": "TASK_PROGRESS",
        "SHOOT": "VISIBLE_COMBAT_TARGET",
        "SWEEP": "VISIBLE_COMBAT_TARGET",
        "PICKUP_BEACON": "VISIBLE_BEACON_PICKUP",
        "HEAL": "SURVIVAL_RECOVERY",
        "REPAIR_SHIELD": "CORE_SHIELD_RECOVERY",
        "SPAWN": "PRODUCTION_TARGET",
    }.get(action_type, "DETERMINISTIC_FALLBACK")


def _build_explanation(
    turn: object,
    plan: object,
    memory: object | None = None,
) -> DecisionExplanation:
    positions = {_raw_id(unit.id): tuple(unit.position) for unit in getattr(turn, "units", ())}
    reason_codes = getattr(memory, "planned_reason_codes", {})
    reason_targets = getattr(memory, "planned_reason_targets", {})
    actions: list[DecisionAction] = []
    acted_ids: set[bytes] = set()
    for identifier, action in sorted(
        getattr(plan, "unit_actions", {}).items(), key=lambda item: _raw_id(item[0])
    ):
        entity_id = _raw_id(identifier)
        acted_ids.add(entity_id)
        action_type = _action_type(action)
        actions.append(
            DecisionAction(
                entity_id=entity_id,
                action_type=action_type,
                reason_code=reason_codes.get(identifier, _reason_for(action_type)),
                risk_before=0,
                risk_after=0,
                target=reason_targets.get(
                    identifier,
                    _target(action, positions.get(entity_id)),
                ),
            )
        )
    core_action = getattr(plan, "core_action", None)
    core = getattr(turn, "core", None)
    if core_action is not None and core is not None:
        action_type = _action_type(core_action)
        actions.append(
            DecisionAction(
                entity_id=_raw_id(core.id),
                action_type=action_type,
                reason_code=reason_codes.get(core.id, _reason_for(action_type)),
                risk_before=0,
                risk_after=0,
                target=reason_targets.get(
                    core.id,
                    _target(core_action, tuple(core.position)),
                ),
            )
        )
        acted_ids.add(_raw_id(core.id))
    for identifier, reason in sorted(reason_codes.items(), key=lambda item: _raw_id(item[0])):
        entity_id = _raw_id(identifier)
        if entity_id in acted_ids or reason not in {
            "SCOUT_WAIT_NO_SAFE_FRONTIER",
            "CONTACT_WAIT_NO_SAFE_RESPONSE",
            "DEFENSE_HOLD",
        }:
            continue
        actions.append(
            DecisionAction(
                entity_id=entity_id,
                action_type="WAIT",
                reason_code=reason,
                risk_before=0,
                risk_after=0,
                target=reason_targets.get(identifier),
            )
        )
    return DecisionExplanation(tuple(actions))


def _fake_plan_from_controller_actions(turn: object) -> object:
    """Expose test-double plans without coupling the tactic to SDK internals."""

    unit_actions: dict[object, object] = {}
    for unit in getattr(turn, "units", ()):
        if not getattr(unit, "actions", ()):
            continue
        name, *values = unit.actions[-1]
        unit_actions[unit.id] = _SimpleAction(name, *values)
    core = getattr(turn, "core", None)
    core_action = None
    if core is not None and getattr(core, "actions", ()):
        name, *values = core.actions[-1]
        core_action = _SimpleAction(name, *values)
    return _SimplePlan(int(getattr(turn, "tick", 0)), unit_actions, core_action)


class _SimpleAction:
    def __init__(self, action_type: object, *values: object) -> None:
        self.type = str(action_type)
        self.direction = next((value for value in values if isinstance(value, Direction)), None)
        self.expected_cell = next(
            (value for value in values if isinstance(value, tuple) and len(value) == 2),
            None,
        )


class _SimplePlan:
    def __init__(self, tick: int, unit_actions: dict[object, object], core_action: object) -> None:
        self.tick = tick
        self.unit_actions = unit_actions
        self.core_action = core_action


def plan_turn(turn: object, memory: object, profile: object) -> PlannerResult:
    """Queue exactly one deterministic plan and return the public result envelope."""

    from balanced_tactic import choose_actions

    memory.policy = profile
    choose_actions(turn, memory)
    try:
        plan = turn.plan
    except AttributeError:
        plan = _fake_plan_from_controller_actions(turn)
    diagnostics = PlannerDiagnostics(
        economy=MappingProxyType(dict(getattr(memory, "economy_diagnostics", {}))),
        defense=MappingProxyType(
            {
                "level": _enum_name(getattr(getattr(memory, "defense", None), "level", "CLEAR")),
                "incoming_damage": int(
                    getattr(getattr(memory, "defense", None), "incoming_damage", 0)
                ),
            }
        ),
        exploration=MappingProxyType(
            dict(getattr(memory, "exploration_diagnostics", {}))
        ),
        contact=MappingProxyType(dict(getattr(memory, "contact_diagnostics", {}))),
    )
    return PlannerResult(
        tick=int(getattr(turn, "tick", 0)),
        plan=plan,
        explanation=_build_explanation(turn, plan, memory),
        diagnostics=diagnostics,
    )


def _enum_name(value: object) -> str:
    name = getattr(value, "name", None)
    if name is not None:
        return str(name).upper()
    raw = getattr(value, "value", value)
    return str(raw).upper().rsplit(".", 1)[-1]


def apply_planner_result(
    turn: object,
    result: PlannerResult,
    *,
    validator: Callable[[object], Any] | None = None,
) -> None:
    """Apply a stored pure result to controls; invalid entities degrade to WAIT."""

    turn.clear()
    if validator is not None:
        try:
            validator(result.plan)
        except (TypeError, ValueError):
            for identifier in getattr(result.plan, "unit_actions", {}):
                turn.unit(identifier).wait()
            return
    for identifier, action in getattr(result.plan, "unit_actions", {}).items():
        controller = turn.unit(identifier)
        action_type = _action_type(action)
        if action_type == "MOVE":
            controller.move(action.direction)
        elif action_type == "WAIT":
            controller.wait()
