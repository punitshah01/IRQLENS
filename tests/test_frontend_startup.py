from pathlib import Path
import time
from urllib.parse import urlparse

import pytest


def test_frontend_defaults_to_overview_without_persisted_view_restore():
    app_js = Path(__file__).resolve().parents[1] / "frontend" / "app.js"
    text = app_js.read_text(encoding="utf-8")

    assert 'view: initialViewFromUrl()' in text
    assert 'storage.get("irqlens:view"' not in text


def test_frontend_is_air_gapped_and_downloads_as_blob():
    frontend = Path(__file__).resolve().parents[1] / "frontend"
    html = (frontend / "index.html").read_text(encoding="utf-8")
    script = (frontend / "app.js").read_text(encoding="utf-8")
    styles = (frontend / "styles.css").read_text(encoding="utf-8")
    for text in (html, script, styles):
        assert "cdn.jsdelivr.net" not in text
        assert "https://" not in text and "http://" not in text
    assert '<script src="/frontend/static/echarts.min.js"></script>' in html
    echarts = frontend / "static" / "echarts.min.js"
    assert echarts.stat().st_size > 500_000
    assert 'version="5.5.1"' in echarts.read_text(encoding="utf-8")
    assert (frontend / "static" / "echarts.LICENSE.txt").is_file()

    assert "await response.blob()" in script
    assert "URL.createObjectURL(blob)" in script and "URL.revokeObjectURL(objectUrl)" in script
    assert ".text()" not in script
    assert "response.status !== 200" in script and "showToast(" in script
    assert "LIVE_RENDER_INTERVAL_MS = 1000" in script
    for element_id in ("sut-switcher", "live-toggle", "irq-topn", "irq-aggregate", "irqbalance-pill", "section-storage", "pcie-table"):
        assert f'id="{element_id}"' in html


@pytest.fixture
def dashboard_page(request):
    pytest.importorskip("pytest_playwright")
    try:
        page = request.getfixturevalue("page")
    except Exception as error:
        # Playwright without a downloaded browser (air-gapped hosts) counts as a missing optional dependency.
        if "Executable doesn't exist" in str(error):
            pytest.skip("no Playwright browser installed; run with --browser-channel msedge or chrome")
        raise
    root = Path(__file__).resolve().parents[1]
    timestamp = time.time()
    interface = '<img src="invalid" onerror="document.body.dataset.injected=1">'
    responses = {
        "/api/health": {"interval_seconds": 1},
        "/api/systems": {"systems": [
            {"id": "sut-test", "hostname": "test-host", "mode": "remote", "status": "ONLINE", "last_seen": timestamp},
            {"id": "sut-two", "hostname": "second-host", "mode": "remote", "status": "STALE", "last_seen": timestamp - 60},
        ]},
        "/api/irq/current": {"rows": []},
        "/api/softirq/current": {"sample": None},
        "/api/network/current": {"global": {}, "interfaces": [{"interface": interface, "rx_bps": 1}]},
        "/api/interfaces": {"interfaces": []},
        "/api/systems/sut-test/visualization": {"timestamp": timestamp, "series": {"irq": [], "network": []}},
        "/api/systems/sut-test/visualization/topology": {"available": False, "reason": "No topology"},
        "/api/systems/sut-test/io": {
            "irqbalance": "active",
            "storage": {
                "devices": [{"device": "nvme0n1", "model": "<b>ssd</b>", "read_iops": 1200, "write_iops": 300, "scheduler": "none", "rotational": False, "numa_node": 1, "msi_irq_count": 9}],
                "series": {"nvme0n1": [{"timestamp": timestamp - 1, "read_iops": 1200, "write_iops": 300, "read_mb_s": 40, "write_mb_s": 10, "avg_read_await_ms": 0.2, "avg_write_await_ms": 0.4, "queue_depth_in_flight": 3}]},
                "totals": {"total_iops": 1500, "read_mb_s": 40, "write_mb_s": 10, "max_await_ms": 0.4},
            },
            "pcie": {
                "link_warnings": 1, "aer_errors": 2, "aer_increasing": 0,
                "devices": [
                    {"bdf": "0000:5e:00.0", "kind": "NIC", "vendor": "0x8086", "device": "0x1592", "driver": "ice", "interfaces": ["ens1f0"], "numa_node": 0,
                     "current_link_speed": "8.0 GT/s PCIe", "current_link_width": 8, "max_link_speed": "16.0 GT/s PCIe", "max_link_width": 16,
                     "link_degraded": True, "msi_irq_count": 65, "aer_dev_correctable": 2, "aer_dev_nonfatal": 0, "aer_dev_fatal": 0, "aer_total": 2},
                    {"bdf": "0000:3b:00.0", "kind": "NVMe", "vendor": "0x8086", "device": "0x0a54", "driver": "nvme", "numa_node": 1,
                     "current_link_speed": "16.0 GT/s PCIe", "current_link_width": 4, "max_link_speed": "16.0 GT/s PCIe", "max_link_width": 4,
                     "link_degraded": False, "msi_irq_count": 9, "aer_dev_correctable": 0, "aer_dev_nonfatal": 0, "aer_dev_fatal": 0, "aer_total": 0},
                ],
            },
            "softnet": {"latest": {"total": {"processed": 10, "dropped": 0, "time_squeeze": 0, "cpu_collision": 0}}, "series": []},
        },
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
        elif path.startswith("/frontend/") and (root / path.lstrip("/")).is_file():
            route.fulfill(path=str(root / path.lstrip("/")))
        else:
            route.abort()

    page.route("http://irqlens.test/**", serve)
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
    assert page.locator('[data-diag-cat="storage"]').is_checked()
    assert page.locator('[data-diag-cat="pcie"]').is_checked()
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


def test_page_loads_vendored_echarts_without_external_requests(dashboard_page):
    page = dashboard_page
    assert page.evaluate("typeof echarts === 'object' && echarts.version") == "5.5.1"
    hosts = page.evaluate("performance.getEntriesByType('resource').map(entry => new URL(entry.name).host)")
    assert set(hosts) == {"irqlens.test"}
    assert page.locator("#irqbalance-pill").inner_text() == "irqbalance: active"


def test_storage_and_pcie_tab(dashboard_page):
    page = dashboard_page
    page.get_by_role("button", name="Storage & PCIe", exact=True).click()
    section = page.locator("#section-storage")
    section.wait_for(state="visible")
    kpis = page.locator("#storage-kpis").inner_text()
    for label in ("TOTAL STORAGE IOPS", "READ MB/S", "WRITE MB/S", "MAX AWAIT LATENCY", "PCIE LINK WARNINGS", "AER ERRORS"):
        assert label in kpis.upper()
    assert "1.5K" in kpis and "0.40 ms" in kpis
    rows = page.locator("#pcie-table tr")
    assert rows.count() == 2
    degraded = rows.nth(0)
    assert "0000:5e:00.0" in degraded.inner_text() and "8.0 GT/s PCIe x8" in degraded.inner_text() and "16.0 GT/s PCIe x16" in degraded.inner_text()
    assert degraded.locator(".tag.bad").inner_text() == "Downgraded"
    assert degraded.locator(".tag.warn").inner_text() == "2 / 0 / 0"
    assert rows.nth(1).locator(".tag").count() == 0
    assert page.locator("#storage-table b").count() == 0 and "<b>ssd</b>" in page.locator("#storage-table").inner_text()
    assert page.evaluate("state.charts.storageIops.getOption().series.map(item => item.name)") == ["nvme0n1 read", "nvme0n1 write"]
    assert page.evaluate("state.charts.storageLatency.getOption().yAxis.length") == 2
    page.get_by_role("button", name="Overview", exact=True).click()
    findings = page.locator("#overview-findings").inner_text()
    assert "PCIe link degraded" in findings and "PCIe AER errors" in findings


def test_sut_switcher_lists_status_and_switches(dashboard_page):
    page = dashboard_page
    options = page.locator("#sut-switcher option").all_inner_texts()
    assert options == ["test-host [ONLINE]", "second-host [STALE]"]
    assert page.locator("#sut-switcher").input_value() == "sut-test"
    with page.expect_request(lambda request: "/api/systems/sut-two/visualization?" in request.url):
        page.locator("#sut-switcher").select_option("sut-two")
    page.wait_for_function("document.getElementById('sut-title').textContent === 'SUT: second-host'")
    assert page.locator("#sut-status-label").inner_text() == "STALE"
    assert page.evaluate("localStorage.getItem('irqlens:host')") == '"sut-two"'


def test_header_status_follows_sut_that_stopped_reporting(dashboard_page):
    page = dashboard_page
    assert page.locator("#sut-status-label").inner_text() == "ONLINE"
    page.route("**/api/systems", lambda route: route.fulfill(json={"systems": [
        {"id": "sut-test", "hostname": "test-host", "mode": "remote", "status": "OFFLINE", "last_seen": time.time() - 120},
    ]}))
    page.wait_for_function("document.getElementById('sut-status-label').textContent === 'OFFLINE'", timeout=10000)
    assert page.locator("#sut-switcher option").all_inner_texts() == ["test-host [OFFLINE]"]


def test_pause_and_resume_live_updates(dashboard_page):
    page = dashboard_page
    toggle = page.locator("#live-toggle")
    assert toggle.inner_text() == "Pause Live Updates" and toggle.get_attribute("aria-pressed") == "false"
    toggle.click()
    assert toggle.inner_text() == "Resume Live Updates" and toggle.get_attribute("aria-pressed") == "true"
    page.evaluate("""() => {
        window.dataLoads = 0;
        const original = loadSelectedData;
        loadSelectedData = (...args) => { window.dataLoads += 1; return original(...args); };
        state.ws.onmessage({ data: JSON.stringify({ type: 'telemetry', sut_id: state.host, timestamp: Date.now() / 1000, cpu_utilization: { 0: 5 } }) });
        state.ws.onmessage({ data: JSON.stringify({ type: 'session_stopped', session_id: 'capture' }) });
    }""")
    page.wait_for_timeout(1300)
    assert page.evaluate("window.dataLoads") == 0
    toggle.click()
    assert toggle.inner_text() == "Pause Live Updates"
    page.wait_for_function("window.dataLoads >= 1")


def test_live_redraw_is_throttled_to_one_second(dashboard_page):
    page = dashboard_page
    draws = page.evaluate("""async () => {
        let count = 0;
        const original = render;
        render = (...args) => { count += 1; return original(...args); };
        const started = Date.now();
        while (Date.now() - started < 2300) {
            state.ws.onmessage({ data: JSON.stringify({ type: 'telemetry', sut_id: state.host, timestamp: Date.now() / 1000, cpu_utilization: { 0: 5 } }) });
            await new Promise(resolve => setTimeout(resolve, 20));
        }
        return count;
    }""")
    assert 1 <= draws <= 3


def test_irq_heatmap_top_n_and_numa_aggregation(dashboard_page):
    page = dashboard_page
    page.get_by_role("button", name="IRQ", exact=True).click()
    page.locator("#section-irq").wait_for(state="visible")
    assert page.locator("#irq-topn").input_value() == "25"
    page.evaluate("""() => {
        state.paused = true;
        state.topology = { available: true, rows: [0, 1, 2, 3].map(cpu => ({ cpu_id: cpu, numa_node: cpu < 2 ? 0 : 1, socket_id: 0 })) };
        const rows = [];
        for (let index = 0; index < 60; index += 1) {
            rows.push({ irq: String(100 + index), irq_name: `q${index}`, total_rate: index < 40 ? 100 - index : 0,
                cpu_rates: index < 40 ? { 0: 10, 3: 90 - index } : {}, numa_node: '0',
                cross_numa_flag: index === 0, handler_numa_nodes: [0, 1] });
        }
        state.snapshot = { irq: { rows }, softirq: {}, network: { interfaces: [] } };
        renderIrq();
    }""")
    labels = page.locator("#chart-irq-heatmap .irq-row-label")
    columns = page.locator("#chart-irq-heatmap .irq-cpu-label")
    assert labels.count() == 25 and columns.count() == 2
    assert labels.first.get_attribute("title") == "q0"
    page.locator("#irq-topn").select_option("all")
    assert labels.count() == 40
    page.locator("#irq-topn").select_option("50")
    assert labels.count() == 40
    assert page.evaluate("localStorage.getItem('irqlens:irqTopN')") == '"50"'
    page.locator("#irq-aggregate").select_option("socket")
    assert columns.count() == 1 and columns.first.get_attribute("title") == "Socket 0"
    page.locator("#irq-aggregate").select_option("numa")
    assert [columns.nth(index).get_attribute("title") for index in range(columns.count())] == ["NUMA 0", "NUMA 1"]
    assert page.locator("#irq-table tr").first.locator(".tag.bad").inner_text() == "Handled on node 0, 1"
    assert page.locator("#irq-table .tag.bad").count() == 1


def test_downloads_use_blob_and_report_http_errors(dashboard_page):
    page = dashboard_page
    body = b"PK\x03\x04\xff\xfe\x00\x80binary-not-utf8\r\n\x00"
    page.route("**/api/sessions/capture/download", lambda route: route.fulfill(
        status=200, body=body, headers={"Content-Type": "application/zip", "Content-Disposition": 'attachment; filename="capture.zip"'},
    ))
    page.route("**/api/sessions/capture/workbook", lambda route: route.fulfill(status=409, json={"detail": "capture is still running"}))
    page.get_by_role("button", name="Collect Logs", exact=True).click()
    page.locator("#section-diagnostics").wait_for(state="visible")
    page.locator('[data-toggle-session="capture"]').click()
    detail = page.locator("#session-detail")
    with page.expect_download() as download_info:
        detail.get_by_role("link", name="Download ZIP", exact=True).click()
    download = download_info.value
    assert download.suggested_filename == "capture.zip"
    assert Path(download.path()).read_bytes() == body
    assert download.url.startswith("blob:")
    assert page.url == "http://irqlens.test/"

    detail.get_by_role("link", name="Download Excel", exact=True).click()
    toast = page.locator("#toast-root .toast.bad")
    toast.wait_for()
    assert toast.inner_text() == "Download failed (HTTP 409): capture is still running"
