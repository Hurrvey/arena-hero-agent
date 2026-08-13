"""SQLite-backed history metric endpoints."""

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/v1")


@router.get("/metrics/summary")
def summary(request: Request) -> dict[str, object]:
    services = request.app.state.services
    return services.metrics.summary(services.session_id)


@router.get("/metrics/series")
def series(request: Request) -> dict[str, object]:
    services = request.app.state.services
    return {
        "points": services.metrics.series(services.session_id),
        "markers": services.runtime_store.event_markers(services.session_id),
    }
