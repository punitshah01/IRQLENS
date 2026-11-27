from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timedelta
from pathlib import Path

import openpyxl
import pytest

from backend.app.services.exporter import ExportEngine

ENVELOPE_KEYS = {"schema_version", "sut_id", "session_id", "captured_at", "sampling_interval_s", "data"}
CAPTURE_START = 1_700_000_000.0


def load_strict(path: Path) -> dict:
    def reject(name: str) -> None:
        raise AssertionError(f"non-finite JSON literal {name} in {path}")

    return json.loads(path.read_text(encoding="utf-8"), parse_constant=reject)


@pytest.fixture
def diagnostic_service(tmp_path):
    from dataclasses import replace
    from backend.app.config import settings
    from backend.app.services.diagnostics import DiagnosticSessionService
    from backend.app.store import SqliteStore

    store = SqliteStore(str(tmp_path / "capture.db"), 100)
    return DiagnosticSessionService(replace(settings, output_dir=tmp_path / "output"), store)


def test_capture_exports_only_selected_categories(diagnostic_service):
    service = diagnostic_service
    session = service.start("selected_system", ["system"], "sut", "Linux", "test", "sut")
    service.stop(session.session_id)
    root = Path(session.output_dir) / "timeseries"
    assert (root / "cpu" / "cpu_utilization_timeseries.json").exists()
    assert not (root / "irq").exists()
    assert not (root / "network").exists()
    assert not (root / "softirq").exists()


def test_stopping_capture_twice_preserves_window(diagnostic_service, monkeypatch):
    service = diagnostic_service
    clock = [100.0]
    monkeypatch.setattr("backend.app.services.diagnostics.time.time", lambda: clock[0])
    session = service.start("stable_window", ["irq"], "sut", "Linux", "test", "sut")
    clock[0] = 105.0
    first = service.stop(session.session_id)
    clock[0] = 120.0
    second = service.stop(session.session_id)
    assert first.end_time == second.end_time == 105.0
    payload = load_strict(Path(session.output_dir) / "timeseries" / "irq" / "irq_timeseries.json")
    assert payload["data"]["end_time"] == 105.0


def test_capture_freezes_sampling_configuration(diagnostic_service, monkeypatch):
    from types import SimpleNamespace

    service = diagnostic_service
    system = SimpleNamespace(sampling_interval_seconds=0.5)
    monkeypatch.setattr(service.store, "get_system", lambda sut_id: system)
    session = service.start("frozen_config", ["network"], "sut", "Linux", "test", "sut", duration_seconds=60)
    system.sampling_interval_seconds = 2.0
    envelope = load_strict(Path(session.output_dir) / "metadata" / "capture_config.json")
    config = envelope["data"]
    assert envelope["sampling_interval_s"] == 0.5
    assert config["sampling_interval_seconds"] == 0.5
    assert config["requested_duration_seconds"] == 60
    assert config["interval_source"] == "agent_registration"


@pytest.mark.parametrize("timestamps,interval,status", [
    (list(range(1, 11)), 1.0, "COMPLETE"),
    (list(range(1, 10)), 1.0, "COMPLETE"),
    ([], 1.0, "INCOMPLETE"),
    ([9, 10], 1.0, "INCOMPLETE"),
    (list(range(1, 11)), None, "INCOMPLETE"),
    ([1, 2, 3, 3, 4, 5, 6, 7, 8, 9, 10], 1.0, "INCOMPLETE"),
    ([1, 2, 3, float("nan"), 11], 1.0, "INCOMPLETE"),
    ([2, 1, 3, 4, 5, 6, 7, 8, 9, 10], 1.0, "INCOMPLETE"),
])
def test_capture_sample_quality(timestamps, interval, status):
    from backend.app.services.capture_quality import sample_quality

    quality = sample_quality(timestamps, 0.0, 10.0, interval)
    assert quality["status"] == status
    assert quality["expected_samples"] == (10 if interval else None)
    if not timestamps:
        assert quality["max_gap_seconds"] == 10.0
        assert quality["coverage_percent"] == 0.0


def test_capture_quality_detects_boundary_gap_despite_sample_count():
    from backend.app.services.capture_quality import sample_quality

    quality = sample_quality([timestamp / 10 for timestamp in range(1, 11)], 0.0, 10.0, 1.0)
    assert quality["coverage_percent"] == 100.0
    assert quality["status"] == "INCOMPLETE"
    assert quality["max_gap_seconds"] == 9.0


def test_empty_capture_is_incomplete_and_metadata_tracks_report(diagnostic_service):
    import zipfile

    service = diagnostic_service
    session = service.start("empty_capture", ["system"], "sut", "Unknown", "test", "local", duration_seconds=60)
    service.stop(session.session_id)
    quality = service.capture_quality(session.session_id)
    assert quality["status"] == "INCOMPLETE"
    assert quality["requested_duration_seconds"] == 60
    assert quality["issue_count"] > 0
    assert quality["scope"] == "capture_quality"
    report = service.generate_html_report(session.session_id)
    assert report.exists()
    report_text = report.read_text(encoding="utf-8")
    assert "Capture Quality" in report_text
    assert "Evidence completeness: INCOMPLETE" in report_text
    assert "Workload/silicon verdict: NOT EVALUATED" in report_text
    root = Path(session.output_dir)
    metadata = load_strict(root / "metadata" / "metadata.json")["data"]
    assert metadata["report_status"] == "ready"
    assert metadata["report_path"] == str(report)
    archive = service.archive_session(session.session_id)
    with zipfile.ZipFile(archive) as bundle:
        assert "capture_quality.json" in bundle.namelist()
        assert "empty_capture.xlsx" in bundle.namelist()
        quality = json.loads(bundle.read("capture_quality.json"))["data"]
        assert quality["status"] == "INCOMPLETE"
        assert any(item["path"] == "report.html" and len(item["sha256"]) == 64 for item in quality["artifacts"])


def test_complete_irq_capture_and_missing_artifact(diagnostic_service, monkeypatch):
    from backend.app.models import IRQSample

    service = diagnostic_service
    clock = [100.0]
    monkeypatch.setattr("backend.app.services.diagnostics.time.time", lambda: clock[0])
    session = service.start("good_irq", ["irq"], "sut", "Linux", "test", "local", duration_seconds=10)
    service.store.add_irq_samples([IRQSample(timestamp=timestamp, sut_ip="local", irq="16", irq_name="test", total_rate=10) for timestamp in range(101, 111)])
    root = Path(session.output_dir)
    files = service._emit_category_files(root / "irq", "irqtop", {"rows": [{"irq": "16"}]}, [{"irq": "16"}])
    service.store.add_session_files(session.session_id, files)
    clock[0] = 110.0
    service.stop(session.session_id)
    quality = service.capture_quality(session.session_id)
    assert quality["status"] == "COMPLETE"
    stream = next(check for check in quality["checks"] if check["name"] == "IRQ samples")
    assert stream["coverage_percent"] == 100.0
    (root / "irq" / "irqtop.csv").unlink()
    quality = service.refresh_capture_quality(session.session_id)
    assert quality["status"] == "INCOMPLETE"
    assert any(check["name"] == "File irq/irqtop.csv" for check in quality["checks"])


def test_json_export_is_strict_enveloped_and_atomic(tmp_path: Path):
    path = tmp_path / "strict.json"
    data = {"rate": float("nan"), "peak": float("inf"), "nested": [float("-inf"), 1.5], 3: "int-key"}
    ExportEngine().write_json(path, data, {"sut_id": "sut-a", "session_id": "capture-1", "sampling_interval_s": 1.0})
    payload = load_strict(path)
    assert set(payload) == ENVELOPE_KEYS
    assert payload["schema_version"] == "1.0"
    assert (payload["sut_id"], payload["session_id"], payload["sampling_interval_s"]) == ("sut-a", "capture-1", 1.0)
    captured_at = datetime.fromisoformat(payload["captured_at"].replace("Z", "+00:00"))
    assert payload["captured_at"].endswith("Z") and captured_at.utcoffset() == timedelta(0)
    assert payload["data"] == {"rate": None, "peak": None, "nested": [None, 1.5], "3": "int-key"}
    assert not list(tmp_path.glob("*.tmp"))


def test_export_formats(tmp_path: Path):
    engine = ExportEngine()
    rows = [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]

    json_path = tmp_path / "out.json"
    csv_path = tmp_path / "out.csv"
    txt_path = tmp_path / "out.txt"

    engine.write_json(json_path, rows)
    engine.write_csv(csv_path, rows)
    engine.write_txt(txt_path, "title", ["line1", "line2"])

    assert load_strict(json_path)["data"] == rows
    assert csv_path.read_text(encoding="utf-8-sig").splitlines() == ["a,b", "1,x", "2,y"]
    assert txt_path.exists() and "line1" in txt_path.read_text(encoding="utf-8")
    assert not hasattr(engine, "write_xml")


def test_csv_export_has_bom_and_flat_safe_cells(tmp_path: Path):
    path = tmp_path / "rows.csv"
    ExportEngine().write_csv(path, [
        {"name": "éth0 – réseau", "ipv4": ["192.0.2.1/24", "192.0.2.2/24"], "total": {"dropped": 3}, "rate": float("nan")},
        {"name": "=cmd|' /C calc'!A0", "ipv4": [], "total": {"dropped": 0}, "rate": -1.5},
    ])
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
    assert list(rows[0]) == ["name", "ipv4", "total.dropped", "rate"]
    assert rows[0] == {"name": "éth0 – réseau", "ipv4": "192.0.2.1/24; 192.0.2.2/24", "total.dropped": "3", "rate": ""}
    assert rows[1]["name"] == "'=cmd|' /C calc'!A0"
    assert rows[1]["rate"] == "-1.5"


def _record_tick(store, timestamp: float) -> None:
    from backend.app.models import IRQSample, NetworkSample, SoftIRQSample

    store.add_irq_samples([IRQSample(
        timestamp=timestamp, sut_ip="local", irq="120", irq_name="IR-PCI-MSI 1-edge ens1f0-TxRx-0", nic="ens1f0",
        source_class="network", direction="TxRx", numa_node="0", affinity_list="0-1", total_rate=100.0,
        cpu_rates={"0": 40.0, "1": 60.0},
    )])
    store.add_softirq_sample(SoftIRQSample(timestamp=timestamp, sut_ip="local", rates={"NET_RX": 50.0, "TIMER": 5.0}))
    store.add_network_samples([NetworkSample(timestamp=timestamp, sut_ip="local", interface="ens1f0", rx_bps=1000.0, tx_bps=500.0)])
    store.add_cpu_utilization("local", {"0": 10.0, "1": float("nan")}, timestamp=timestamp)
    store.add_softnet("local", {
        "available": True, "interval_s": 1.0,
        "total": {"processed": 10, "dropped": 1, "time_squeeze": 2, "cpu_collision": 0},
        "per_cpu": {"1": {"processed": 10, "dropped": 1, "time_squeeze": 2, "cpu_collision": 0}},
    }, timestamp)
    store.add_storage("local", [{
        "device": "nvme0n1", "read_iops": 100.0, "write_iops": 50.0, "read_mb_s": 1.0, "write_mb_s": 0.5,
        "avg_read_await_ms": 0.5, "avg_write_await_ms": 2.0, "queue_depth_in_flight": 7.0, "util_pct": 50.0,
        "model": "TEST SSD", "scheduler": "none", "nr_requests": 1023, "rotational": False, "numa_node": 1,
        "msi_irqs": ["130", "131"], "msi_irq_count": 2,
    }], timestamp)


@pytest.fixture
def populated_capture(diagnostic_service, monkeypatch):
    from backend.app.models import CPUTopologyEntry

    service = diagnostic_service
    clock = [CAPTURE_START]
    monkeypatch.setattr("backend.app.services.diagnostics.time.time", lambda: clock[0])
    categories = ["irq", "softirq", "network", "system", "storage", "pcie"]
    session = service.start("xlsx_capture", categories, "sut", "Linux", "6.8", "local", duration_seconds=30)
    service.store.add_cpu_topology("local", [CPUTopologyEntry(cpu_id=0, numa_node=0), CPUTopologyEntry(cpu_id=1, numa_node=1)], timestamp=CAPTURE_START)
    for offset in (1.0, 2.0, 3.0):
        _record_tick(service.store, CAPTURE_START + offset)
    service.store.add_pcie("local", [{
        "bdf": "0000:5e:00.0", "kind": "NIC", "vendor": "0x8086", "device": "0x1592", "class": "0x020000", "driver": "ice",
        "interfaces": ["ens1f0"], "numa_node": 0, "current_link_speed": "8.0 GT/s PCIe", "max_link_speed": "16.0 GT/s PCIe",
        "current_link_width": 8, "max_link_width": 16, "link_degraded": True, "msi_irqs": ["120", "121"], "msi_irq_count": 2,
        "aer_dev_correctable": 2, "aer_dev_nonfatal": 0, "aer_dev_fatal": 0, "aer_total": 2, "aer_increasing": False,
    }], CAPTURE_START + 1.0)
    service.collect_snapshot(session.session_id, categories, sut_id="local")
    clock[0] = CAPTURE_START + 4.0
    service.stop(session.session_id)
    return service, Path(session.output_dir)


def test_every_capture_json_is_strict_and_enveloped(populated_capture):
    _, root = populated_capture
    files = sorted(root.rglob("*.json"))
    assert {"summary.json", "capture_quality.json", "cpu_utilization_timeseries.json", "storage_timeseries.json", "softnet_timeseries.json"} <= {path.name for path in files}
    for path in files:
        payload = load_strict(path)
        assert set(payload) == ENVELOPE_KEYS, path
        assert payload["schema_version"] == "1.0"
        assert payload["session_id"] == "xlsx_capture" and payload["sut_id"] == "local"
        assert payload["sampling_interval_s"] == 1.0
    cpu = load_strict(root / "timeseries" / "cpu" / "cpu_utilization_timeseries.json")["data"]
    assert cpu["samples"][0]["cpus"] == {"0": 10.0, "1": None}
    assert not list(root.rglob("*.tmp")) and not list(root.rglob("*.xml"))


def test_capture_workbook_is_flat_and_complete(populated_capture):
    from backend.app.services.workbook import SHEET_NAMES

    _, root = populated_capture
    workbook = openpyxl.load_workbook(root / "xlsx_capture.xlsx")
    assert workbook.sheetnames == list(SHEET_NAMES) == [
        "Summary", "IRQ_Top", "SoftIRQ", "Network", "Softnet", "Storage", "PCIe_Cards", "TimeSeries_Metrics",
    ]
    tables = {}
    for sheet in workbook.worksheets:
        headers = [cell.value for cell in sheet[1]]
        assert headers and all(isinstance(header, str) and header for header in headers), sheet.title
        assert all(cell.font.bold for cell in sheet[1])
        assert sheet.freeze_panes == "A2"
        assert sheet.max_row >= 2, f"{sheet.title} has no data rows"
        rows = list(sheet.iter_rows(min_row=2, values_only=True))
        for row in rows:
            for value in row:
                assert value is None or isinstance(value, (str, int, float, bool)), (sheet.title, value)
                if isinstance(value, str):
                    assert not value.lstrip().startswith(("{", "[")), (sheet.title, value)
        tables[sheet.title] = [dict(zip(headers, row)) for row in rows]

    irq = tables["IRQ_Top"][0]
    assert "cpu_rates" not in irq
    assert irq["irq"] == "120" and irq["nic"] == "ens1f0"
    assert irq["total_irq_s"] == pytest.approx(100.0)
    assert (irq["top_cpu"], irq["top_cpu_irq_s"]) == ("1", pytest.approx(60.0))
    assert (irq["numa_node"], irq["cross_numa_flag"], irq["active_cpu_count"]) == ("0", True, 2)

    assert {row["class"] for row in tables["SoftIRQ"]} == {"NET_RX", "TIMER"}
    assert tables["Network"][0]["interface"] == "ens1f0"
    assert tables["Softnet"][0] == {"cpu": "ALL", "processed": 30, "dropped": 3, "time_squeeze": 6, "cpu_collision": 0}
    storage = tables["Storage"][0]
    assert storage["device"] == "nvme0n1" and storage["avg_read_iops"] == pytest.approx(100.0)
    assert (storage["scheduler"], storage["rotational"], storage["msi_irq_count"]) == ("none", False, 2)
    card = tables["PCIe_Cards"][0]
    assert card["bdf"] == "0000:5e:00.0" and card["link_degraded"] is True and card["interfaces"] == "ens1f0"
    series = tables["TimeSeries_Metrics"]
    assert [row["epoch_s"] for row in series] == [CAPTURE_START + 1.0, CAPTURE_START + 2.0, CAPTURE_START + 3.0]
    assert series[0]["irq_total_s"] == pytest.approx(100.0) and series[0]["cpu_util_avg_pct"] is None
    assert series[0]["timestamp_utc"] == "2023-11-14T22:13:21.000Z"
    summary = {row["metric"]: row["value"] for row in tables["Summary"]}
    assert summary["session_name"] == "xlsx_capture" and summary["cross_numa_irqs"] == 1 and summary["pcie_link_warnings"] == 1

    irq_sheet = workbook["IRQ_Top"]
    formats = {irq_sheet.cell(row=1, column=index).value: irq_sheet.cell(row=2, column=index).number_format for index in range(1, irq_sheet.max_column + 1)}
    assert formats["total_irq_s"] == "0.00" and formats["active_cpu_count"] == "#,##0"
    assert irq_sheet.column_dimensions["B"].width >= len("IR-PCI-MSI 1-edge ens1f0-TxRx-0")


def test_irq_snapshot_csv_is_flat(populated_capture):
    _, root = populated_capture
    rows = list(csv.DictReader(io.StringIO((root / "irq" / "irqtop.csv").read_bytes().decode("utf-8-sig"))))
    assert rows[0]["total_irq_s"] == "100.0" and rows[0]["top_cpu"] == "1" and rows[0]["cross_numa_flag"] == "True"
    assert not any(key.startswith("cpu_rates") for key in rows[0])
    raw = load_strict(root / "irq" / "irqtop.json")["data"]
    assert raw["rows"][0]["cpu_rates"] == {"0": 40.0, "1": 60.0}


def test_session_exports_all_formats_and_raw_commands(tmp_path: Path):
    from dataclasses import replace
    from backend.app.config import settings
    from backend.app.services.diagnostics import DiagnosticSessionService
    from backend.app.store import SqliteStore

    store = SqliteStore(str(tmp_path / "test.db"), 100)
    service = DiagnosticSessionService(replace(settings, output_dir=tmp_path), store)
    files = service._emit_category_files(tmp_path / "system", "system", {"hostname": "test"}, [{"hostname": "test"}])
    assert {item.format for item in files} == {"json", "csv", "txt"}
    assert not (tmp_path / "system" / "system.xml").exists()
    commands = [{"command": "ip route", "exit_code": 0, "stdout": "default via 192.0.2.1", "stderr": ""}]
    files = service._emit_command_files(tmp_path / "commands", commands)
    assert len(files) == 4
    assert "default via 192.0.2.1" in (tmp_path / "commands" / "command_001.txt").read_text(encoding="utf-8")
