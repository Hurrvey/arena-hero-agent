import json

from playwright.sync_api import Page, expect

from tests.e2e.test_dashboard import install_api_mocks


def test_pause_and_stop_wait_for_server_confirmation(
    page: Page,
    live_server_url: str,
) -> None:
    install_api_mocks(page)

    def controls(route):
        action = route.request.url.rsplit("/", 1)[-1]
        status = {"pause": "PAUSED", "stop": "STOPPED", "resume": "RUNNING"}.get(action, "RUNNING")
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"runtimeId": "test", "status": status, "lastTick": 1234}))

    page.route("**/api/v1/agent/pause", controls)
    page.route("**/api/v1/agent/stop", controls)
    page.goto(live_server_url + "/")

    page.get_by_role("button", name="暂停").click()
    expect(page.get_by_text("已暂停")).to_be_visible()
    page.get_by_role("button", name="停止").click()
    expect(page.get_by_text("已停止")).to_be_visible()


def test_accepted_and_resolved_have_distinct_labels_and_icons(
    page: Page,
    live_server_url: str,
) -> None:
    install_api_mocks(page)
    page.goto(live_server_url + "/")

    expect(page.get_by_text("ACCEPTED", exact=True).first).to_be_visible()
    expect(page.get_by_text("RESOLVED", exact=True)).to_be_visible()
