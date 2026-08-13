"""Read-only adaptive status placeholder backed by the service contract."""

from fastapi import APIRouter

router = APIRouter(prefix="/api/v1")


@router.get("/adaptive/status")
def status() -> dict[str, object]:
    return {"enabled": False, "autoApply": False, "status": "DISABLED"}


@router.get("/adaptive/reports")
def reports() -> dict[str, object]:
    return {"items": []}
