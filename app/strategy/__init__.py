"""Pure, deterministic strategy primitives shared by the runtime and UI."""

from .models import CellRisk, EntityKind, EntitySnapshot, Position
from .movement import (
    MoveCandidate,
    MoveIntent,
    MovementDependency,
    MovementResolution,
    RejectedMove,
    resolve_movement,
)
from .planner import DecisionAction, DecisionExplanation, PlannerDiagnostics, PlannerResult
from .planner_adapter import apply_planner_result, plan_turn
from .projection import CapacityProjection, compute_capacity_projection, should_defer_deposit
from .risk import VisibleRiskMap, build_visible_risk_map, risk_at
from .visibility import VisibilityMap, compute_visible_cells, supercover_cells

__all__ = [
    "CapacityProjection",
    "CellRisk",
    "DecisionAction",
    "DecisionExplanation",
    "EntityKind",
    "EntitySnapshot",
    "MoveCandidate",
    "MoveIntent",
    "MovementDependency",
    "MovementResolution",
    "PlannerDiagnostics",
    "PlannerResult",
    "Position",
    "RejectedMove",
    "VisibilityMap",
    "VisibleRiskMap",
    "apply_planner_result",
    "build_visible_risk_map",
    "compute_capacity_projection",
    "compute_visible_cells",
    "plan_turn",
    "resolve_movement",
    "risk_at",
    "should_defer_deposit",
    "supercover_cells",
]
