"""Read-only adaptive window and review status API."""

import os
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from adaptive_strategy import SkillBundle, SkillBundleError
from app.adaptive.coordinator import apply_persisted_candidate
from app.errors import AppError

router = APIRouter(prefix="/api/v1")


class CandidateDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    action: Literal["APPLY", "REJECT"]
    expected_revision: int = Field(alias="expectedRevision", ge=1)


def _enabled() -> bool:
    return bool(os.environ.get("ARENA_HERO_LLM_API_KEY"))


@router.get("/adaptive/status")
def status(request: Request) -> dict[str, object]:
    windows = request.app.state.services.adaptive.windows(limit=1)
    latest = windows[0] if windows else None
    return {
        "enabled": _enabled(),
        "autoApply": os.environ.get("ARENA_HERO_ADAPTIVE_AUTO_APPLY", "0") == "1",
        "status": latest.status if latest is not None else ("READY" if _enabled() else "DISABLED"),
        "skillFingerprint": latest.skill_fingerprint if latest is not None else None,
        "minimumSamples": 30,
    }


@router.get("/adaptive/reports")
def reports(request: Request) -> dict[str, object]:
    services = request.app.state.services
    candidates = {item["cycleId"]: item for item in services.adaptive.candidates()}
    current_fingerprint = None
    try:
        current_fingerprint = SkillBundle.load().fingerprint
    except SkillBundleError:
        current_fingerprint = None
    return {
        "items": [
            _report(
                window,
                candidates.get(window.cycle_id),
                current_fingerprint,
                services.strategies,
            )
            for window in services.adaptive.windows()
        ]
    }


def _report(
    window,
    candidate,
    current_fingerprint: str | None,
    strategies,
) -> dict[str, object]:
    result: dict[str, object] = {
        "cycleId": window.cycle_id,
        "startTick": window.start_tick,
        "endTick": window.end_tick,
        "sampleCount": window.sample_count,
        "baseRevision": window.base_revision,
        "candidateRevision": window.candidate_revision,
        "skillFingerprint": window.skill_fingerprint,
        "rawScore": window.raw_score,
        "scorePerTick": window.normalized_score,
        "status": candidate["status"] if candidate else window.status,
        "changes": [],
    }
    if candidate:
        result["candidateId"] = candidate["candidateId"]
        try:
            base = strategies.get(window.base_revision).profile
        except LookupError:
            base = None
        current = candidate["profile"]
        if base is not None:
            result["changes"] = [
                {"field": name, "before": before, "after": current[name]}
                for name, before in base.to_mapping().items()
                if current.get(name) != before
            ]
    try:
        active_revision = strategies.current().revision
    except LookupError:
        active_revision = None
    try:
        pending = strategies.pending()
        pending_revision = pending.revision if pending is not None else None
    except LookupError:
        pending_revision = None
    if window.candidate_revision is not None and window.candidate_revision == active_revision:
        result["status"] = "APPLIED"
    elif window.candidate_revision is not None and window.candidate_revision == pending_revision:
        result["status"] = "PENDING_ACTIVATION"
    elif result["status"] in {"APPLIED", "REJECTED"}:
        pass
    elif active_revision is not None and window.base_revision != active_revision:
        result["status"] = "STALE"
        result["disabledReason"] = "基准策略版本已变化，候选已过期"
    elif current_fingerprint and current_fingerprint != window.skill_fingerprint:
        result["status"] = "STALE"
        result["disabledReason"] = "Skill 指纹已变化，候选已过期"
    elif window.sample_count < 30:
        result["disabledReason"] = "样本数量不足"
    return result


@router.post("/adaptive/candidates/{candidate_id}")
def decide_candidate(
    candidate_id: str,
    payload: CandidateDecision,
    request: Request,
) -> dict[str, object]:
    services = request.app.state.services
    try:
        candidate = services.adaptive.candidate(candidate_id)
    except LookupError as exc:
        raise AppError("ADAPTIVE_CANDIDATE_NOT_FOUND", "Candidate was not found", 404) from exc
    if payload.action == "REJECT":
        if not services.adaptive.reject_candidate(candidate_id):
            raise AppError(
                "ADAPTIVE_CANDIDATE_STATE_CONFLICT",
                "Candidate has already created a strategy revision",
                409,
                {"status": candidate["status"]},
            )
        return {"candidateId": candidate_id, "status": "REJECTED"}
    state = (
        services.runtime_store.current_state(services.session_id) if services.session_id else None
    )
    defense = str((state or {}).get("defenseLevel", (state or {}).get("threat", "CLEAR")))
    try:
        fingerprint = SkillBundle.load().fingerprint
    except SkillBundleError as exc:
        raise AppError(
            "ADAPTIVE_SKILL_UNAVAILABLE",
            "The bundled Arena Hero skill is unavailable",
            503,
        ) from exc
    result = apply_persisted_candidate(
        repository=services.adaptive,
        strategies=services.strategies,
        candidate_id=candidate_id,
        expected_revision=payload.expected_revision,
        current_defense=defense,
        current_fingerprint=fingerprint,
    )
    if not result["applied"]:
        raise AppError(
            "ADAPTIVE_CANDIDATE_BLOCKED",
            "Candidate failed current safety checks",
            409,
            {"reason": result["reason"]},
        )
    return {
        "candidateId": candidate_id,
        "status": "PENDING_ACTIVATION",
        "revision": result["revision"],
    }
