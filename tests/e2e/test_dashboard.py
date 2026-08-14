import json
from urllib.parse import urlsplit

from playwright.sync_api import Page, expect


def install_api_mocks(page: Page) -> None:
    state = {
        "tick": 1234,
        "resources": 19,
        "resourceCapacity": 20,
        "population": 4,
        "defenseLevel": "APPROACH",
        "core": {"id": "CORE", "kind": "CORE", "position": [12, 8], "hp": 5, "shield": 4},
        "units": [
            {"id": "W1", "unitType": "WORKER", "position": [8, 9], "hp": 2, "cargo": 1},
            {"id": "R1", "unitType": "RANGER", "position": [16, 6], "hp": 2},
        ],
        "visibleEnemies": [{"id": "E1", "unitType": "RANGER", "position": [20, 6], "hp": 2}],
        "resourceCells": [[5, 4], [7, 4], [18, 12]],
        "obstacleCells": [[10, 5], [10, 6], [10, 7]],
        "beacon": {"position": [22, 3], "status": "GROUND"},
        "visibility": {
            "tick": 1234,
            "currentCells": [[12, 8], [13, 8], [13, 12], [16, 6], [20, 6]],
            "explorationRevision": 4,
        },
    }
    plan = {
        "tick": 1234,
        "status": "ACCEPTED",
        "plan": {
            "tick": 1234,
            "unitActions": {"R1": {"type": "SHOOT", "expectedCell": [20, 6]}},
        },
        "explanation": {
            "actions": [
                {
                    "entityId": "R1",
                    "actionType": "SHOOT",
                    "reasonCode": "VISIBLE_COMBAT_TARGET",
                    "riskBefore": 1,
                    "riskAfter": 0,
                }
            ]
        },
    }
    payloads = {
        "/api/v1/agent/status": {"runtimeId": "test", "status": "RUNNING", "lastTick": 1234},
        "/api/v1/state/current": state,
        "/api/v1/plan/current": plan,
        "/api/v1/events?tail=true&limit=300": {
            "events": [
                {
                    "schemaVersion": 1,
                    "seq": 1,
                    "type": "plan.accepted",
                    "at": "2026-08-13T00:00:00Z",
                    "runtimeId": "test",
                    "tick": 1234,
                    "payload": {},
                }
            ],
            "lastSeq": 1,
        },
        "/api/v1/metrics/summary": {"ticks": 1234},
        "/api/v1/strategy": {"revision": 17, "profile": {}},
        "/api/v1/adaptive/status": {"enabled": False},
    }

    def route_api(route):
        request_url = urlsplit(route.request.url)
        url = request_url.path
        if request_url.query:
            url += "?" + request_url.query
        payload = payloads.get(url)
        if payload is None and url.startswith("/api/v1/exploration?"):
            payload = {
                "revision": 4,
                "bounds": {"minX": 0, "minY": 0, "maxX": 30, "maxY": 20},
                "exploredCells": [
                    [x, y]
                    for x in range(4, 24)
                    for y in range(2, 14)
                ],
                "knownObstacleCells": [[10, 5], [10, 6], [10, 7]],
            }
        if payload is None and url.startswith("/api/v1/events"):
            payload = {"events": [], "lastSeq": 1}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload or {}))

    page.route("**/api/v1/**", route_api)


def test_dashboard_renders_runtime_metrics_plan_events_and_units(
    page: Page,
    live_server_url: str,
) -> None:
    install_api_mocks(page)
    page.goto(live_server_url + "/")

    expect(page.get_by_text("运行中")).to_be_visible()
    expect(page.get_by_text("1234", exact=True).first).to_be_visible()
    expect(page.get_by_text("APPROACH")).to_be_visible()
    expect(page.get_by_text("ACCEPTED", exact=True).first).to_be_visible()
    expect(page.get_by_text("R1", exact=True).first).to_be_visible()
    expect(page.locator('tr[data-entity-id="R1"]')).to_contain_text("SHOOT")
    expect(page.locator("#tactical-map")).to_be_visible()


def test_empty_dashboard_connects_live_before_the_first_turn(
    page: Page,
    live_server_url: str,
) -> None:
    page.set_default_timeout(4_000)
    with page.expect_websocket() as websocket_info:
        page.goto(live_server_url + "/")

    assert websocket_info.value.url.endswith("/ws/v1/live?afterSeq=0")
    expect(page.locator("#toast")).to_be_hidden()
    expect(page.get_by_text("已停止")).to_be_visible()


def test_enemy_removed_from_new_snapshot_disappears_from_unit_details(
    page: Page,
    live_server_url: str,
) -> None:
    install_api_mocks(page)
    page.goto(live_server_url + "/")
    expect(page.get_by_text("E1", exact=True)).to_have_count(0)


def test_disconnected_snapshot_is_visibly_stale(page: Page, live_server_url: str) -> None:
    install_api_mocks(page)
    page.goto(live_server_url + "/")
    expect(page.get_by_text("运行中")).to_be_visible()
    page.context.set_offline(True)
    expect(page.get_by_text("显示最后一次权威快照", exact=False)).to_be_visible()


def test_dashboard_labels_current_explored_and_unknown_fog(
    page: Page,
    live_server_url: str,
) -> None:
    install_api_mocks(page)
    page.goto(live_server_url + "/")

    expect(page.get_by_text("当前可见", exact=True)).to_be_visible()
    expect(page.get_by_text("已探索", exact=True)).to_be_visible()
    expect(page.get_by_text("未探索", exact=True)).to_be_visible()
    expect(page.locator("#map-description")).to_contain_text(
        "已探索不代表当前安全"
    )


def test_tactical_map_has_three_visually_distinct_fog_states(
    page: Page,
    live_server_url: str,
) -> None:
    install_api_mocks(page)
    page.goto(live_server_url + "/")
    expect(page.locator("#map-description")).to_contain_text("已探索暗区 235 格")

    luminance = page.locator("#tactical-map").evaluate(
        """canvas => {
          const rect = canvas.getBoundingClientRect();
          const scaleX = canvas.width / rect.width;
          const scaleY = canvas.height / rect.height;
          const center = [canvas.width / 2, canvas.height / 2];
          const origin = [12, 8];
          const cell = 30;
          const sample = ([x, y]) => {
            const px = Math.round(center[0] + (x - origin[0]) * cell * scaleX + 5);
            const py = Math.round(center[1] + (y - origin[1]) * cell * scaleY + 5);
            const [r, g, b] = canvas.getContext("2d").getImageData(px, py, 1, 1).data;
            return .2126 * r + .7152 * g + .0722 * b;
          };
          return {
            visible: sample([13, 12]),
            explored: sample([14, 12]),
            unknown: sample([14, 14]),
          };
        }"""
    )

    assert luminance["visible"] >= luminance["explored"] + 10
    assert luminance["explored"] >= luminance["unknown"] + 6
