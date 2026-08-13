"""History metric endpoints."""

from fastapi import APIRouter

router = APIRouter(prefix="/api/v1")


@router.get("/metrics/summary")
def summary() -> dict[str, object]:
    return {"ticks": 0, "resources": 0, "beaconTicks": 0}


@router.get("/metrics/series")
def series() -> dict[str, object]:
    return {"points": []}
