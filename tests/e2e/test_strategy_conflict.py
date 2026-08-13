"""Strategy editor CAS and draft-preservation browser tests."""

from __future__ import annotations

import json
from urllib.parse import urlsplit

from playwright.sync_api import Page, expect

from tests.e2e.test_dashboard import install_api_mocks

PROFILE = {
    "schema_version": 1,
    "beacon_priority": 1.0,
    "economy_priority": 1.0,
    "combat_priority": 0.75,
    "defense_priority": 1.0,
    "worker_target": 23,
    "bootstrap_worker_target": 6,
    "near_beacon_radius": 12,
    "runner_stall_ticks": 6,
    "resource_memory_ttl": 64,
    "resource_stall_ticks": 6,
    "scout_ring_step": 10,
    "ranger_ratio": 2.0,
    "carrier_safety_margin": 0,
    "spawn_aggression": 0.5,
    "defender_vanguard_target": 1,
    "defender_ranger_target": 2,
    "defense_watch_radius": 5,
    "worker_evacuation_radius": 3,
}


def install_strategy_mocks(page: Page, *, conflict_once: bool = False) -> None:
    install_api_mocks(page)
    state = {"revision": 7, "status": "ACTIVE", "profile": dict(PROFILE)}
    attempts = 0

    def route(route) -> None:
        nonlocal attempts
        path = urlsplit(route.request.url).path
        if path == "/api/v1/strategy/schema":
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"fields": {"worker_target": {"minimum": 2, "maximum": 23}}}),
            )
            return
        if route.request.method == "PUT":
            attempts += 1
            submitted = json.loads(route.request.post_data or "{}")
            if conflict_once and attempts == 1:
                server_profile = {**PROFILE, "combat_priority": 1.0}
                route.fulfill(
                    status=409,
                    content_type="application/json",
                    body=json.dumps(
                        {
                            "code": "STRATEGY_REVISION_CONFLICT",
                            "message": "changed",
                            "details": {
                                "current": {
                                    "revision": 8,
                                    "status": "ACTIVE",
                                    "profile": server_profile,
                                }
                            },
                        }
                    ),
                )
                return
            state.update(
                revision=9,
                status="PENDING",
                profile=submitted["profile"],
                activatedTick=None,
            )
        route.fulfill(status=200, content_type="application/json", body=json.dumps(state))

    page.route("**/api/v1/strategy/schema", route)
    page.route("**/api/v1/strategy", route)


def test_revision_conflict_keeps_draft_and_merges_server_changes(
    page: Page,
    live_server_url: str,
) -> None:
    install_strategy_mocks(page, conflict_once=True)
    page.goto(live_server_url + "/strategy")
    worker = page.get_by_label("成熟 Worker 目标")
    worker.fill("20")
    page.get_by_role("button", name="保存为待激活版本").click()

    expect(page.get_by_text("检测到版本冲突")).to_be_visible()
    expect(worker).to_have_value("20")
    expect(page.get_by_label("战斗优先级")).to_have_value("1")


def test_saved_profile_waits_for_turn_boundary_activation(
    page: Page,
    live_server_url: str,
) -> None:
    install_strategy_mocks(page)
    page.goto(live_server_url + "/strategy")
    page.get_by_label("成熟 Worker 目标").fill("22")
    page.get_by_role("button", name="保存为待激活版本").click()

    expect(page.get_by_text("等待下一个 Tick 边界激活")).to_be_visible()
