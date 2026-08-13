"""Adaptive evaluation, history chart, and safe settings browser tests."""

from __future__ import annotations

import json
from urllib.parse import urlsplit

from playwright.sync_api import Page, expect

from tests.e2e.test_dashboard import install_api_mocks


def install_secondary_view_mocks(page: Page) -> None:
    install_api_mocks(page)
    payloads = {
        "/api/v1/adaptive/status": {
            "enabled": True,
            "autoApply": False,
            "status": "REVIEW_REQUIRED",
            "skillFingerprint": "a1b2c3d4e5f6",
            "minimumSamples": 30,
        },
        "/api/v1/adaptive/reports": {
            "items": [
                {
                    "candidateId": "candidate-stale",
                    "cycleId": "cycle-1",
                    "startTick": 100,
                    "endTick": 160,
                    "sampleCount": 60,
                    "rawScore": 180,
                    "scorePerTick": 3.0,
                    "status": "STALE",
                    "skillFingerprint": "old-fingerprint",
                    "changes": [{"field": "economy_priority", "before": 1, "after": 1.15}],
                    "disabledReason": "Skill 指纹已变化，候选已过期",
                }
            ]
        },
        "/api/v1/strategy": {"revision": 7, "status": "ACTIVE", "profile": {}},
        "/api/v1/metrics/series": {
            "points": [
                {"tick": 101, "resources": 3, "population": 2, "beaconOwned": 0},
                {"tick": 107, "resources": 8, "population": 3, "beaconOwned": 1},
                {"tick": 130, "resources": 12, "population": 5, "beaconOwned": 1},
            ],
            "markers": [{"tick": 107, "eventType": "beacon.captured"}],
        },
        "/api/v1/settings": {
            "rawRetentionDays": 7,
            "eventRetentionDays": 30,
            "logLevel": "INFO",
            "providerConfigured": True,
            "providerHost": "api.openai.com",
            "model": "gpt-5.4",
            "mapRefresh": "LIVE_EVENT_DRIVEN",
        },
    }

    def route(route) -> None:
        path = urlsplit(route.request.url).path
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(payloads.get(path, {})),
        )

    for path in payloads:
        page.route(f"**{path}", route)


def test_reviewable_candidate_can_be_applied_after_server_confirmation(
    page: Page,
    live_server_url: str,
) -> None:
    install_api_mocks(page)
    page.route(
        "**/api/v1/adaptive/status",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"enabled": True, "status": "REVIEW_REQUIRED"}),
        ),
    )
    page.route(
        "**/api/v1/adaptive/reports",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "items": [
                        {
                            "candidateId": "candidate-1",
                            "startTick": 100,
                            "endTick": 160,
                            "sampleCount": 60,
                            "rawScore": 180,
                            "scorePerTick": 3,
                            "status": "REVIEW_REQUIRED",
                            "skillFingerprint": "current",
                            "changes": [],
                        }
                    ]
                }
            ),
        ),
    )
    page.route(
        "**/api/v1/adaptive/candidates/candidate-1",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {"candidateId": "candidate-1", "status": "PENDING_ACTIVATION", "revision": 8}
            ),
        ),
    )
    page.goto(live_server_url + "/adaptive")

    page.get_by_role("button", name="应用候选").click()

    expect(page.get_by_text("候选已进入待激活版本")).to_be_visible()


def test_candidate_shows_samples_score_fingerprint_diff_and_disabled_reason(
    page: Page,
    live_server_url: str,
) -> None:
    install_secondary_view_mocks(page)
    page.goto(live_server_url + "/adaptive")

    expect(page.get_by_text("60 个样本")).to_be_visible()
    expect(page.get_by_text("3.00 / Tick")).to_be_visible()
    expect(page.get_by_text("old-fingerprint")).to_be_visible()
    expect(page.get_by_text("economy_priority")).to_be_visible()
    expect(page.get_by_role("button", name="应用候选")).to_be_disabled()
    expect(page.get_by_role("button", name="拒绝")).to_be_enabled()
    expect(page.get_by_text("Skill 指纹已变化")).to_be_visible()


def test_history_uses_discrete_tick_axis_and_event_markers(
    page: Page,
    live_server_url: str,
) -> None:
    install_secondary_view_mocks(page)
    page.goto(live_server_url + "/history")

    expect(page.locator("#history-chart")).to_be_visible()
    expect(page.get_by_text("Tick 101")).to_be_visible()
    expect(page.locator("#history-chart").get_by_text("Tick 107")).to_be_visible()
    expect(page.get_by_text("beacon.captured")).to_be_visible()


def test_settings_never_render_keys_or_edit_base_url(
    page: Page,
    live_server_url: str,
) -> None:
    install_secondary_view_mocks(page)
    page.goto(live_server_url + "/settings")

    expect(page.get_by_text("api.openai.com")).to_be_visible()
    expect(page.get_by_text("gpt-5.4")).to_be_visible()
    expect(page.get_by_text("API Key", exact=False)).to_have_count(0)
    expect(page.get_by_label("Base URL")).to_have_count(0)
