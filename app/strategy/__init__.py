"""Pure, deterministic strategy primitives shared by the runtime and UI."""

from .models import CellRisk, EntityKind, EntitySnapshot, Position
from .risk import VisibleRiskMap, build_visible_risk_map, risk_at
from .visibility import VisibilityMap, compute_visible_cells, supercover_cells

__all__ = [
    "CellRisk",
    "EntityKind",
    "EntitySnapshot",
    "Position",
    "VisibilityMap",
    "VisibleRiskMap",
    "build_visible_risk_map",
    "compute_visible_cells",
    "risk_at",
    "supercover_cells",
]
