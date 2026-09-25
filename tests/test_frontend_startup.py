from pathlib import Path
import time
from urllib.parse import urlparse

import pytest


def test_frontend_defaults_to_overview_without_persisted_view_restore():
    app_js = Path(__file__).resolve().parents[1] / "frontend" / "app.js"
    text = app_js.read_text(encoding="utf-8")

    assert 'view: initialViewFromUrl()' in text
    assert 'storage.get("irqlens:view"' not in text


@pytest.fixture
def dashboard_page(request):
    pytest.importorskip("pytest_playwright")
    page = request.getfixturevalue("page")
    root = Path(__file__).resolve().parents[1]
    timestamp = time.time()
    interface = '<img src="invalid" onerror="document.body.dataset.injected=1">'
    responses = {
        "/api/health": {"interval_seconds": 1},
        "/api/systems": {"systems": [{"id": "sut-test", "hostname": "test-host", "mode": "remote", "status": "ONLINE", "last_seen": timestamp}]},
        "/api/irq/current": {"rows": []},
        "/api/softirq/current": {"sample": None},
        "/api/network/current": {"global": {}, "interfaces": [{"interface": interface, "rx_bps": 1}]},
        "/api/interfaces": {"interfaces": []},
        "/api/systems/sut-test/visualization": {"timestamp": timestamp, "series": {"irq": [], "network": []}},
        "/api/systems/sut-test/visualization/topology": {"available": False, "reason": "No topology"},
        "/api/sessions": {"sessions": []},
        "/api/sessions/capture": {
            "session_id": "capture", "sut_id": "sut-test", "hostname": "test-host", "status": "stopped",
            "output_dir": "C:/Users/operator/AppData/Local/Temp/irqlens-preview/output/long_capture_name_for_mobile_validation",
            "start_time": timestamp - 30, "end_time": timestamp, "categories": ["routes"],
            "remote_diagnostics": [{"job_id": "job-test", "status": "failed", "error": "Tool unavailable <b>not markup</b>"}],
        },
        "/api/sessions/capture/files": {"files": []},
    }
    responses["/api/sessions"]["sessions"] = [responses["/api/sessions/capture"]]

    def serve(route):
        path = urlparse(route.request.url).path
        if path.startswith("/api/"):
            route.fulfill(json=responses.get(path, {}))
        elif path == "/":
            route.fulfill(path=str(root / "frontend" / "index.html"), content_type="text/html")
        elif path in {"/frontend/app.js", "/frontend/styles.css"}:
            route.fulfill(path=str(root / path.lstrip("/")))
        else:
            route.abort()

    page.route("http://irqlens.test/**", serve)
    page.route("https://cdn.jsdelivr.net/**", lambda route: route.abort())
    page.route_web_socket("ws://irqlens.test/ws", lambda socket: None)
    page.goto("http://irqlens.test/")
    page.wait_for_function("document.getElementById('sut-title').textContent === 'SUT: test-host'")
    return page


def test_frontend_escapes_telemetry_and_navigates(dashboard_page):
    page = dashboard_page
    assert page.locator("#overview-findings img").count() == 0
    assert "<img" in page.locator("#overview-findings").inner_text()
    assert page.evaluate("document.body.dataset.injected") is None
    page.get_by_role("button", name="Collect Logs", exact=True).click()
    page.locator("#section-diagnostics").wait_for(state="visible")
    assert page.locator("#section-diagnostics").is_visible()
    assert page.locator('[data-diag-cat="routes"]').is_visible()
    assert page.locator('[data-diag-cat="sockets"]').is_visible()
    assert page.get_by_role("button", name="Admin", exact=True).count() == 0


def test_frontend_displays_remote_command_failures(dashboard_page):
    page = dashboard_page
    page.get_by_role("button", name="Collect Logs", exact=True).click()
    page.locator("#section-diagnostics").wait_for(state="visible")
    page.evaluate("async () => { await loadSessionDetail('capture'); renderSessions(); }")
    detail = page.locator("#session-detail")
    assert "Remote Commands" in detail.inner_text()
    assert "failed" in detail.inner_text()
    assert "Tool unavailable <b>not markup</b>" in detail.inner_text()
    assert detail.locator("b").count() == 0


def test_time_presets_select_and_request_history(dashboard_page):
    page = dashboard_page
    for seconds in ["30", "60", "300", "900", "1800", "3600"]:
        with page.expect_request(lambda request: f"window_seconds={seconds}&" in request.url):
            page.locator(f'[data-range="{seconds}"]').click()
        assert page.locator(".time-chip.active").get_attribute("data-range") == seconds
        assert page.evaluate("localStorage.getItem('irqlens:timeRange')") == f'"{seconds}"'
    page.locator('[data-range="custom"]').click()
    assert page.locator("#custom-range").is_visible()
    assert page.locator(".time-chip.active").get_attribute("data-range") == "custom"
    page.locator('[data-range="30"]').click()
    assert not page.locator("#custom-range").is_visible()


def test_view_capture_scrolls_to_loaded_details(dashboard_page):
    page = dashboard_page
    page.set_viewport_size({"width": 390, "height": 700})
    page.get_by_role("button", name="Collect Logs", exact=True).click()
    page.locator("#section-diagnostics").wait_for(state="visible")
    page.evaluate("() => { state.diag.completedSessionId = 'capture'; renderDiagnostics(); bindDiagnosticsActions(); }")
    page.get_by_role("button", name="View Capture", exact=True).click()
    page.wait_for_function("document.getElementById('session-detail').getBoundingClientRect().top >= -1 && document.getElementById('session-detail').getBoundingClientRect().top < innerHeight / 2")
    assert "Session capture" in page.locator("#session-detail").inner_text()
    assert page.locator('[data-toggle-session="capture"]').get_attribute("aria-expanded") == "true"


def test_generate_report_and_view_link(dashboard_page):
    page = dashboard_page
    report = {"session_id": "capture", "status": "ready", "report_path": "/output/capture/report.html"}
    page.route("**/api/sessions/capture/report", lambda route: route.fulfill(json=report if route.request.method == "GET" else {"status": "pending"}))
    page.route("**/api/sessions/capture", lambda route: route.fulfill(json={
        "session_id": "capture", "hostname": "test-host", "status": "stopped",
        "report_status": "ready", "report_path": report["report_path"], "categories": [],
    }))
    page.get_by_role("button", name="Collect Logs", exact=True).click()
    page.locator("#section-diagnostics").wait_for(state="visible")
    page.locator('[data-toggle-session="capture"]').click()
    page.locator("#session-generate-report").wait_for(state="visible")
    with page.expect_request(lambda request: request.url.endswith("/report") and request.method == "POST"):
        page.locator("#session-generate-report").click()
    page.wait_for_function("!state.reportingSessions.capture")
    link = page.locator("#session-detail").get_by_role("link", name="View Report", exact=True)
    assert link.get_attribute("href").endswith("/api/sessions/capture/report/view")
    assert page.locator("#session-detail").get_by_role("link", name="Download Report", exact=True).get_attribute("href").startswith("http://irqlens.test/api/files?")


def test_generate_report_displays_server_error(dashboard_page):
    page = dashboard_page
    page.route("**/api/sessions/capture/report", lambda route: route.fulfill(status=409, json={"detail": "Capture is still running."}))
    page.get_by_role("button", name="Collect Logs", exact=True).click()
    page.locator("#section-diagnostics").wait_for(state="visible")
    page.locator('[data-toggle-session="capture"]').click()
    page.locator("#session-generate-report").click()
    page.get_by_text("Capture is still running.", exact=True).first.wait_for()
    assert "Capture is still running." in page.locator("#session-detail").inner_text()


@pytest.mark.parametrize("status", ["COMPLETE", "INCOMPLETE"])
def test_capture_quality_action(dashboard_page, status):
    page = dashboard_page
    page.set_viewport_size({"width": 390, "height": 844})
    page.route("**/api/sessions/capture/quality", lambda route: route.fulfill(json={
        "status": status, "scope": "capture_quality", "generated_at": time.time(),
        "checks": [{"name": "Network samples", "status": status, "unique_samples": 2,
                    "expected_samples": 30, "coverage_percent": 6.7, "max_gap_seconds": 20,
                    "reasons": ["Sample gap <b>not markup</b>"] if status == "INCOMPLETE" else []}],
    }))
    page.get_by_role("button", name="Collect Logs", exact=True).click()
    page.locator("#section-diagnostics").wait_for(state="visible")
    page.locator('[data-toggle-session="capture"]').click()
    section = page.locator("#capture-quality")
    section.get_by_text("Not evaluated", exact=True).wait_for()
    section.get_by_role("button", name="Check Evidence").click()
    section.locator("strong.tag").filter(has_text=status).wait_for()
    assert "NOT EVALUATED" in section.inner_text()
    assert "2 / 30" in section.inner_text()
    assert "6.7%" in section.inner_text()
    assert section.locator("b").count() == 0
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_capture_quality_error_is_visible(dashboard_page):
    page = dashboard_page
    page.route("**/api/sessions/capture/quality", lambda route: route.fulfill(status=409, json={"detail": "Capture is still running."}))
    page.get_by_role("button", name="Collect Logs", exact=True).click()
    page.locator("#section-diagnostics").wait_for(state="visible")
    page.locator('[data-toggle-session="capture"]').click()
    page.get_by_role("button", name="Check Evidence").click()
    page.locator("#capture-quality [role=alert]").wait_for()
    assert "Capture is still running." in page.locator("#capture-quality").inner_text()
