"""Bounded, serializable strategy settings and deterministic score calculation."""

from __future__ import annotations

from dataclasses import dataclass
import math
from collections.abc import Mapping
from typing import Any


PROFILE_BOUNDS = {
    "beacon_priority": (0.75, 1.50),
    "economy_priority": (0.75, 1.50),
    "combat_priority": (0.50, 1.25),
    "ranger_ratio": (1.0, 3.0),
    "spawn_aggression": (0.0, 1.0),
}

PROFILE_INT_BOUNDS = {
    # Keep older persisted profiles loadable while defaulting new deployments
    # to the mature 23-Worker economy.
    "worker_target": (2, 23),
    "bootstrap_worker_target": (4, 8),
    "near_beacon_radius": (4, 24),
    "runner_stall_ticks": (3, 12),
    "resource_memory_ttl": (32, 128),
    "resource_stall_ticks": (3, 12),
    "scout_ring_step": (6, 20),
    "carrier_safety_margin": (0, 1),
}

_DEFAULTS = {
    "schema_version": 1,
    "beacon_priority": 1.0,
    "economy_priority": 1.0,
    "combat_priority": 0.75,
    "worker_target": 23,
    "bootstrap_worker_target": 6,
    "near_beacon_radius": 12,
    "runner_stall_ticks": 6,
    "resource_memory_ttl": 64,
    "resource_stall_ticks": 6,
    "scout_ring_step": 10,
    "ranger_ratio": 2.0,
    "carrier_safety_margin": 0,
    "spawn_aggression": 0.5,
}


@dataclass(frozen=True)
class StrategyProfile:
    schema_version: int = 1
    beacon_priority: float = 1.0
    economy_priority: float = 1.0
    combat_priority: float = 0.75
    worker_target: int = 23
    bootstrap_worker_target: int = 6
    near_beacon_radius: int = 12
    runner_stall_ticks: int = 6
    resource_memory_ttl: int = 64
    resource_stall_ticks: int = 6
    scout_ring_step: int = 10
    ranger_ratio: float = 2.0
    carrier_safety_margin: int = 0
    spawn_aggression: float = 0.5

    def __post_init__(self) -> None:
        self.validate()

    @classmethod
    def default(cls) -> "StrategyProfile":
        return cls()

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, object]) -> "StrategyProfile":
        if not isinstance(mapping, Mapping):
            raise ValueError("profile must be a mapping")
        if any(not isinstance(key, str) for key in mapping):
            raise ValueError("profile field names must be strings")
        unknown = set(mapping) - set(_DEFAULTS)
        if unknown:
            raise ValueError(f"unknown profile fields: {sorted(unknown)!r}")
        values = dict(_DEFAULTS)
        values.update(mapping)
        profile = cls(**values)
        profile.validate()
        return profile

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "beacon_priority": self.beacon_priority,
            "economy_priority": self.economy_priority,
            "combat_priority": self.combat_priority,
            "worker_target": self.worker_target,
            "bootstrap_worker_target": self.bootstrap_worker_target,
            "near_beacon_radius": self.near_beacon_radius,
            "runner_stall_ticks": self.runner_stall_ticks,
            "resource_memory_ttl": self.resource_memory_ttl,
            "resource_stall_ticks": self.resource_stall_ticks,
            "scout_ring_step": self.scout_ring_step,
            "ranger_ratio": self.ranger_ratio,
            "carrier_safety_margin": self.carrier_safety_margin,
            "spawn_aggression": self.spawn_aggression,
        }

    def with_updates(self, **changes: object) -> "StrategyProfile":
        """Return a validated profile with selected fields changed."""
        return StrategyProfile.from_mapping({**self.to_mapping(), **changes})

    def validate(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("unsupported schema_version")
        for name, (minimum, maximum) in PROFILE_BOUNDS.items():
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be numeric")
            if not math.isfinite(float(value)) or not minimum <= value <= maximum:
                raise ValueError(f"{name} outside bounds")
        for name, (minimum, maximum) in PROFILE_INT_BOUNDS.items():
            value = getattr(self, name)
            if type(value) is not int:
                raise ValueError(f"{name} must be an integer")
            if not minimum <= value <= maximum:
                raise ValueError(f"{name} outside bounds")


def internal_score(metrics: Mapping[str, float]) -> float:
    """Calculate the deterministic weighted score from observed metrics."""
    weights = {
        "beacon_ticks": 10.0,
        "resources_harvested": 1.0,
        "resources_deposited": 1.0,
        "resources_captured": 1.0,
        "damage_dealt": 1.0,
        "core_participations": 20.0,
        "units_lost": -4.0,
        "core_losses": -100.0,
        "failed_actions": -0.5,
        # Economic dead time is deliberately visible to the adaptive layer.
        # These weights are strong enough to reject a live-lock without
        # overpowering real Beacon, resource, or combat results.
        "zero_resource_ticks": -0.25,
        "idle_worker_ticks": -0.25,
        "route_stalls": -1.0,
        "oscillation_ticks": -2.0,
        "runner_progress_ticks": 0.25,
    }
    total = 0.0
    for name, weight in weights.items():
        value: Any = metrics.get(name, 0.0)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"metric {name} must be numeric")
        if not math.isfinite(float(value)):
            raise ValueError(f"metric {name} must be finite")
        if float(value) < 0:
            raise ValueError(f"metric {name} must be non-negative")
        total += float(value) * weight
    return total
