"""Public, immutable output shared by the CLI runtime and Web console."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
from typing import Any


@dataclass(frozen=True, slots=True)
class DecisionAction:
    entity_id: bytes
    action_type: str
    reason_code: str
    risk_before: int
    risk_after: int
    target: tuple[int, int] | None = None

    def public_mapping(self) -> dict[str, object]:
        return {
            "entity_id": self.entity_id.hex(),
            "action_type": self.action_type,
            "reason_code": self.reason_code,
            "risk_before": self.risk_before,
            "risk_after": self.risk_after,
            "target": list(self.target) if self.target is not None else None,
        }


@dataclass(frozen=True, slots=True)
class DecisionExplanation:
    actions: tuple[DecisionAction, ...] = ()


@dataclass(frozen=True, slots=True)
class PlannerDiagnostics:
    economy: Mapping[str, object] = field(default_factory=dict)
    defense: Mapping[str, object] = field(default_factory=dict)
    exploration: Mapping[str, object] = field(default_factory=dict)
    contact: Mapping[str, object] = field(default_factory=dict)
    rejected_moves: tuple[Mapping[str, object], ...] = ()


@dataclass(frozen=True, slots=True)
class PlannerResult:
    tick: int
    plan: Any
    explanation: DecisionExplanation
    diagnostics: PlannerDiagnostics

    def public_mapping(self) -> dict[str, object]:
        plan = self.plan
        if hasattr(plan, "model_dump"):
            raw_plan = plan.model_dump(mode="json")
        else:
            raw_plan = _json_safe(plan)
        return {
            "tick": self.tick,
            "plan": raw_plan,
            "explanation": [action.public_mapping() for action in self.explanation.actions],
            "diagnostics": {
                "economy": _json_safe(self.diagnostics.economy),
                "defense": _json_safe(self.diagnostics.defense),
                "exploration": _json_safe(self.diagnostics.exploration),
                "contact": _json_safe(self.diagnostics.contact),
                "rejected_moves": _json_safe(self.diagnostics.rejected_moves),
            },
        }


def _json_safe(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in sorted(value.items(), key=str)}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if hasattr(value, "__dict__"):
        return _json_safe(vars(value))
    return value
