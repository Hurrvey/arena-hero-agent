"""Read-only adaptive window and review status API."""

import os

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/v1")


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
    return {
        "items": [
            {
                "cycleId": window.cycle_id,
                "startTick": window.start_tick,
                "endTick": window.end_tick,
                "sampleCount": window.sample_count,
                "baseRevision": window.base_revision,
                "candidateRevision": window.candidate_revision,
                "skillFingerprint": window.skill_fingerprint,
                "rawScore": window.raw_score,
                "scorePerTick": window.normalized_score,
                "status": window.status,
                "changes": [],
            }
            for window in request.app.state.services.adaptive.windows()
        ]
    }
