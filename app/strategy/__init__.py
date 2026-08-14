"""Pure, deterministic strategy primitives shared by the runtime and UI."""

from .exploration import (
    CHUNK_SIZE,
    MASK_BYTES,
    ChunkKey,
    ExplorationChunk,
    ExplorationDelta,
    ExplorationMap,
    ExplorationWindow,
    bit_index,
    chunk_key,
)
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
    "CHUNK_SIZE",
    "CapacityProjection",
    "CellRisk",
    "ChunkKey",
    "DecisionAction",
    "DecisionExplanation",
    "EntityKind",
    "EntitySnapshot",
    "ExplorationChunk",
    "ExplorationDelta",
    "ExplorationMap",
    "ExplorationWindow",
    "MASK_BYTES",
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
    "bit_index",
    "build_visible_risk_map",
    "compute_capacity_projection",
    "compute_visible_cells",
    "chunk_key",
    "plan_turn",
    "resolve_movement",
    "risk_at",
    "should_defer_deposit",
    "supercover_cells",
]
