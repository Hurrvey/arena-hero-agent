"""FastAPI application factory for the loopback Arena Hero Agent console."""

from __future__ import annotations

import mimetypes
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from strategy_policy import StrategyProfile

from .api import adaptive, agent, metrics, state, strategy, websocket
from .api.dependencies import Services
from .api.websocket import CommittedEventBroadcaster
from .config import Settings
from .errors import AppError
from .runtime.models import RuntimeConflict
from .runtime.service_factory import build_runtime_manager
from .storage import Database, RuntimeStore, StrategyRepository


def _error_payload(
    request: Request,
    code: str,
    message: str,
    details: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "code": code,
        "message": message,
        "requestId": request.state.request_id,
        "details": details or {},
    }


def create_app(
    settings: Settings | None = None,
    services: dict[str, object] | None = None,
) -> FastAPI:
    configured = settings or Settings()
    injected = services or {}
    database = injected.get("database") or Database(configured.database_path)
    runtime_store = injected.get("runtime_store") or RuntimeStore(database)
    strategies = injected.get("strategies") or StrategyRepository(database)
    broadcaster = injected.get("broadcaster") or CommittedEventBroadcaster(
        configured.websocket_client_queue
    )
    runtime_factory = None
    if "runtime_manager" in injected:
        runtime_manager = injected["runtime_manager"]
    else:
        runtime_manager, runtime_factory = build_runtime_manager(
            settings=configured,
            runtime_store=runtime_store,
            strategies=strategies,
            broadcaster=broadcaster,
        )
    container = Services(
        configured,
        database,
        runtime_store,
        strategies,
        runtime_manager,
        broadcaster,
        runtime_factory=runtime_factory,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database.initialize()
        strategies.ensure_initial(StrategyProfile.default())
        app.state.services = container
        yield
        try:
            runtime_manager.stop()
        except (RuntimeConflict, AttributeError):
            pass

    app = FastAPI(
        title="Arena Hero Agent",
        version="1.1.0",
        lifespan=lifespan,
    )
    app.state.services = container

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        request.state.request_id = request.headers.get("x-request-id") or uuid4().hex
        response = await call_next(request)
        response.headers["x-request-id"] = request.state.request_id
        response.headers["x-content-type-options"] = "nosniff"
        response.headers["x-frame-options"] = "DENY"
        response.headers["referrer-policy"] = "no-referrer"
        response.headers["content-security-policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; "
            "script-src 'self'; connect-src 'self' ws: wss:; object-src 'none'"
        )
        return response

    @app.exception_handler(AppError)
    async def app_error(request: Request, exc: AppError):
        return JSONResponse(
            _error_payload(request, exc.code, exc.message, exc.details),
            status_code=exc.status_code,
        )

    @app.exception_handler(RuntimeConflict)
    async def runtime_conflict(request: Request, _exc: RuntimeConflict):
        return JSONResponse(
            _error_payload(
                request,
                "RUNTIME_STATE_CONFLICT",
                "The requested runtime transition is not available",
            ),
            status_code=409,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return JSONResponse(
            _error_payload(
                request,
                "VALIDATION_ERROR",
                "The request payload is invalid",
                {"errors": exc.errors(include_url=False)},
            ),
            status_code=422,
        )

    app.include_router(agent.router)
    app.include_router(state.router)
    app.include_router(strategy.router)
    app.include_router(adaptive.router)
    app.include_router(metrics.router)
    app.include_router(websocket.router)

    if configured.asset_directory.is_dir():
        app.mount(
            "/assets/arena-hero",
            StaticFiles(directory=configured.asset_directory),
            name="arena-hero-assets",
        )
    if configured.static_directory.is_dir():
        app.mount(
            "/assets/app",
            StaticFiles(directory=configured.static_directory),
            name="app-assets",
        )

    approved_routes = {"/", "/strategy", "/adaptive", "/history", "/settings"}

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        route = "/" + path
        if route not in approved_routes:
            return JSONResponse({"code": "NOT_FOUND"}, status_code=404)
        index = configured.static_directory / "index.html"
        if not index.is_file():
            return JSONResponse({"code": "UI_NOT_BUILT"}, status_code=404)
        mimetypes.add_type("text/javascript", ".js")
        return FileResponse(index)

    return app


app = create_app()
