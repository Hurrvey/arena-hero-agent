"""Current state, plan, and committed service event queries."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from app.errors import AppError

router = APIRouter(prefix="/api/v1")


@router.get("/state/current")
def current_state(request: Request) -> dict[str, object]:
    services = request.app.state.services
    state = (
        services.runtime_store.current_state(services.session_id) if services.session_id else None
    )
    if state is None:
        raise AppError("STATE_NOT_AVAILABLE", "No authoritative Turn is available", 404)
    return state


@router.get("/plan/current")
def current_plan(request: Request) -> dict[str, object]:
    services = request.app.state.services
    plan = services.runtime_store.current_plan(services.session_id) if services.session_id else None
    if plan is None:
        raise AppError("PLAN_NOT_AVAILABLE", "No current plan is available", 404)
    return plan


@router.get("/events")
def events(
    request: Request,
    after_seq: int = Query(default=0, alias="afterSeq", ge=0),
    limit: int = Query(default=200, ge=1, le=1000),
) -> dict[str, object]:
    page = request.app.state.services.runtime_store.events_after(after_seq, limit=limit)
    return {
        "events": [
            {
                "seq": event.seq,
                "sessionId": event.session_id,
                "tick": event.tick,
                "type": event.event_type,
                "payload": event.payload,
                "createdAt": event.created_at,
            }
            for event in page.events
        ],
        "lastSeq": page.last_seq,
    }
