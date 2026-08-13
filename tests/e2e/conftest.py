"""Real-browser fixtures for the local tactical console."""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator

import httpx
import pytest
import uvicorn
from playwright.sync_api import Browser, Page, Playwright, sync_playwright

from app.config import PROJECT_ROOT, Settings
from app.main import create_app


@pytest.fixture(scope="session")
def live_server_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """Serve the actual SPA without reading local secrets or production data."""

    data_dir = tmp_path_factory.mktemp("dashboard-server")
    settings = Settings(
        database_path=data_dir / "dashboard.db",
        lock_directory=data_dir / "locks",
        static_directory=PROJECT_ROOT / "frontend",
        asset_directory=PROJECT_ROOT / "arena-hero-ui-assets",
        dotenv_path=data_dir / "missing.env",
        legacy_adaptive_directory=data_dir / "missing-adaptive",
    )
    application = create_app(settings)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])

    server = uvicorn.Server(
        uvicorn.Config(
            application,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            access_log=False,
        )
    )
    thread = threading.Thread(target=server.run, name="dashboard-test-server", daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            if httpx.get(base_url + "/", timeout=0.25).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.05)
    else:
        server.should_exit = True
        thread.join(timeout=2)
        raise RuntimeError("dashboard test server did not become ready")

    yield base_url
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="session")
def browser_runtime() -> Iterator[tuple[Playwright, Browser]]:
    """Launch one isolated headless Chromium instance for the E2E suite."""

    runtime = sync_playwright().start()
    browser = runtime.chromium.launch(headless=True)
    yield runtime, browser
    browser.close()
    runtime.stop()


@pytest.fixture
def page(browser_runtime: tuple[Playwright, Browser]) -> Iterator[Page]:
    """Give each test a clean 1440p browser context."""

    _runtime, browser = browser_runtime
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    page = context.new_page()
    yield page
    context.close()
