"""Bounded, account-hidden exploration viewport endpoint."""

from __future__ import annotations

import hashlib

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse

from app.errors import AppError

router = APIRouter(prefix="/api/v1", tags=["exploration"])


@router.get("/exploration")
def exploration_window(
    request: Request,
    min_x: int = Query(alias="minX"),
    min_y: int = Query(alias="minY"),
    max_x: int = Query(alias="maxX"),
    max_y: int = Query(alias="maxY"),
):
    services = request.app.state.services
    factory = services.runtime_factory
    scope = getattr(factory, "account_scope", None)
    if not services.session_id or not scope:
        raise AppError(
            "EXPLORATION_NOT_AVAILABLE",
            "Exploration is unavailable before the first runtime session",
            404,
        )

    width = max_x - min_x + 1
    height = max_y - min_y + 1
    if (
        width < 1
        or height < 1
        or width > 96
        or height > 96
        or width * height > 96 * 96
    ):
        raise AppError(
            "EXPLORATION_WINDOW_INVALID",
            "Exploration bounds must describe at most a 96 by 96 window",
            422,
        )
    try:
        window = services.exploration.window(
            scope,
            min_x=min_x,
            min_y=min_y,
            max_x=max_x,
            max_y=max_y,
        )
    except ValueError as exc:
        raise AppError(
            "EXPLORATION_WINDOW_INVALID",
            "Exploration bounds are invalid",
            422,
        ) from exc

    token = hashlib.sha256(
        f"{window.revision}:{min_x}:{min_y}:{max_x}:{max_y}".encode("ascii")
    ).hexdigest()
    etag = f'"{token}"'
    headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return JSONResponse(
        {
            "revision": window.revision,
            "bounds": {
                "minX": min_x,
                "minY": min_y,
                "maxX": max_x,
                "maxY": max_y,
            },
            "exploredCells": [list(cell) for cell in window.explored_cells],
            "knownObstacleCells": [
                list(cell) for cell in window.known_obstacle_cells
            ],
        },
        headers=headers,
    )
