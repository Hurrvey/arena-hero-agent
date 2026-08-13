"""Responsive layout checks for the tactical console."""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from app.config import PROJECT_ROOT
from tests.e2e.test_dashboard import install_api_mocks


@pytest.mark.parametrize(
    ("width", "height"),
    [(1440, 1000), (1024, 900), (768, 900), (390, 844)],
)
def test_dashboard_has_no_page_level_horizontal_overflow(
    page: Page,
    live_server_url: str,
    width: int,
    height: int,
) -> None:
    install_api_mocks(page)
    page.set_viewport_size({"width": width, "height": height})
    page.goto(live_server_url + "/")

    expect(page.locator("#tactical-map")).to_be_visible()
    expect(page.get_by_text("运行中")).to_be_visible()
    dimensions = page.evaluate(
        "() => ({ viewport: window.innerWidth, page: document.documentElement.scrollWidth })"
    )
    assert dimensions["page"] <= dimensions["viewport"] + 1

    output = Path(PROJECT_ROOT, "test-results", "dashboard")
    output.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=output / f"overview-{width}.png", full_page=True)
