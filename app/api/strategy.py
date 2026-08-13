"""Immutable strategy revision API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from app.errors import AppError
from app.storage.strategy_repository import RevisionConflict
from strategy_policy import StrategyProfile

router = APIRouter(prefix="/api/v1")


class StrategyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    expected_revision: int = Field(alias="expectedRevision", ge=1)
    profile: dict[str, Any]
    reason: str = Field(min_length=1, max_length=500)


def _public(revision) -> dict[str, object]:
    return {
        "revision": revision.revision,
        "source": revision.source,
        "parentRevision": revision.parent_revision,
        "profile": revision.profile.to_mapping(),
        "reason": revision.reason,
        "activatedTick": revision.activated_tick,
        "status": revision.status,
        "createdAt": revision.created_at,
    }


@router.get("/strategy")
def get_strategy(request: Request) -> dict[str, object]:
    return _public(request.app.state.services.strategies.current())


@router.put("/strategy")
def update_strategy(payload: StrategyUpdate, request: Request) -> dict[str, object]:
    try:
        profile = StrategyProfile.from_mapping(payload.profile)
        revision = request.app.state.services.strategies.create_revision(
            expected_revision=payload.expected_revision,
            profile=profile,
            source="MANUAL",
            reason=payload.reason,
        )
    except RevisionConflict as exc:
        raise AppError(
            "STRATEGY_REVISION_CONFLICT",
            "The active strategy changed or another revision is pending",
            409,
        ) from exc
    return _public(revision)


@router.get("/strategy/history")
def history(request: Request) -> dict[str, object]:
    current = request.app.state.services.strategies.current()
    pending = request.app.state.services.strategies.pending()
    return {"items": [_public(item) for item in (current, pending) if item is not None]}
