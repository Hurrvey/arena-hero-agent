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


def test_mobile_navigation_keeps_all_five_pages_reachable(
    page: Page,
    live_server_url: str,
) -> None:
    install_api_mocks(page)
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(live_server_url + "/")

    navigation = page.get_by_role("navigation", name="主导航")
    expect(navigation).to_be_visible()
    expect(navigation.get_by_role("link")).to_have_count(5)
    navigation.get_by_role("link", name="策略", exact=True).click()

    expect(page.get_by_role("heading", name="策略控制台", exact=True)).to_be_visible()


def test_mobile_map_keeps_complete_fog_legend_visible(
    page: Page,
    live_server_url: str,
) -> None:
    install_api_mocks(page)
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(live_server_url + "/")

    legend = page.locator(".map-legend")
    expect(legend).to_be_visible()
    expect(legend.get_by_text("当前可见", exact=True)).to_be_visible()
    expect(legend.get_by_text("已探索", exact=True)).to_be_visible()
    expect(legend.get_by_text("未探索", exact=True)).to_be_visible()
    assert legend.evaluate("element => element.getBoundingClientRect().bottom") <= page.locator(
        ".map-stage"
    ).evaluate("element => element.getBoundingClientRect().bottom")
