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
from .risk import VisibleRiskMap, build_visible_risk_map, risk_at
from .visibility import VisibilityMap, compute_visible_cells, supercover_cells

__all__ = [
    "CellRisk",
    "EntityKind",
    "EntitySnapshot",
    "MoveCandidate",
    "MoveIntent",
    "MovementDependency",
    "MovementResolution",
    "Position",
    "RejectedMove",
    "VisibilityMap",
    "VisibleRiskMap",
    "build_visible_risk_map",
    "compute_visible_cells",
    "resolve_movement",
    "risk_at",
    "supercover_cells",
]
