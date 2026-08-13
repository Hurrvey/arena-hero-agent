"""Conservative post-combat population and Core storage projection."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .models import CellRisk, EntitySnapshot, Position


@dataclass(frozen=True, slots=True)
class CapacityProjection:
    current_population: int
    projected_population_floor: int
    current_capacity: int
    projected_capacity: int
    projected_overflow: int
    visibly_doomed_unit_ids: tuple[bytes, ...]


def _capacity(population: int) -> int:
    return max(10, population * 5)


def compute_capacity_projection(
    units: Sequence[EntitySnapshot],
    *,
    risk_map: Mapping[Position, CellRisk],
    planned_destinations: Mapping[bytes, Position],
    current_resources: int,
    pending_deposit: int = 0,
) -> CapacityProjection:
    """Project the lower storage cap caused by currently visible lethal attacks."""

    if current_resources < 0 or pending_deposit < 0:
        raise ValueError("resource amounts must be non-negative")
    living = tuple(unit for unit in units if unit.hp > 0)
    doomed = tuple(
        sorted(
            unit.entity_id
            for unit in living
            if risk_map.get(
                planned_destinations.get(unit.entity_id, unit.position),
                CellRisk(),
            ).expected_damage
            >= unit.hp
        )
    )
    population = len(living)
    projected_population = max(0, population - len(doomed))
    current_capacity = _capacity(population)
    projected_capacity = _capacity(projected_population)
    overflow = max(0, current_resources + pending_deposit - projected_capacity)
    return CapacityProjection(
        current_population=population,
        projected_population_floor=projected_population,
        current_capacity=current_capacity,
        projected_capacity=projected_capacity,
        projected_overflow=overflow,
        visibly_doomed_unit_ids=doomed,
    )


def should_defer_deposit(projection: CapacityProjection) -> bool:
    """Prefer preserving Worker cargo when a deposit would be visibly destroyed."""

    return projection.projected_overflow > 0
