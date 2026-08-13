"""Agent lifecycle endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/v1")


def _snapshot(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return value
    status = getattr(value, "status", "STOPPED")
    return {
        "runtimeId": str(getattr(value, "runtime_id", "")),
        "status": str(getattr(status, "value", status)),
        "lastTick": getattr(value, "last_tick", None),
        "errorCode": getattr(value, "error_code", None),
        "errorMessage": getattr(value, "error_message", None),
        "submittedTicks": int(getattr(value, "submitted_ticks", 0)),
    }


@router.get("/health")
def health() -> dict[str, object]:
    return {"status": "ready", "service": "arena-hero-agent"}


@router.get("/agent/status")
def status(request: Request) -> dict[str, object]:
    return _snapshot(request.app.state.services.runtime_manager.status())


@router.post("/agent/start")
def start(request: Request) -> dict[str, object]:
    manager = request.app.state.services.runtime_manager
    snapshot = manager.start()
    runtime_factory = request.app.state.services.runtime_factory
    if runtime_factory is not None:
        request.app.state.services.session_id = runtime_factory.session_id
    return _snapshot(snapshot)


@router.post("/agent/pause")
def pause(request: Request) -> dict[str, object]:
    return _snapshot(request.app.state.services.runtime_manager.pause())


@router.post("/agent/resume")
def resume(request: Request) -> dict[str, object]:
    return _snapshot(request.app.state.services.runtime_manager.resume())


@router.post("/agent/stop")
def stop(request: Request) -> dict[str, object]:
    return _snapshot(request.app.state.services.runtime_manager.stop())


@router.post("/agent/retry")
def retry(request: Request) -> dict[str, object]:
    request.app.state.services.runtime_manager.stop()
    return start(request)
