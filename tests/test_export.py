from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET
import json

import pytest

from backend.app.services.exporter import ExportEngine


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
    payload = json.loads((Path(session.output_dir) / "timeseries" / "irq" / "irq_timeseries.json").read_text())
    assert payload["end_time"] == 105.0


def test_capture_freezes_sampling_configuration(diagnostic_service, monkeypatch):
    from types import SimpleNamespace

    service = diagnostic_service
    system = SimpleNamespace(sampling_interval_seconds=0.5)
    monkeypatch.setattr(service.store, "get_system", lambda sut_id: system)
    session = service.start("frozen_config", ["network"], "sut", "Linux", "test", "sut", duration_seconds=60)
    system.sampling_interval_seconds = 2.0
    config = json.loads((Path(session.output_dir) / "metadata" / "capture_config.json").read_text())
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
    metadata = json.loads((root / "metadata" / "metadata.json").read_text())
    assert metadata["report_status"] == "ready"
    assert metadata["report_path"] == str(report)
    archive = service.archive_session(session.session_id)
    with zipfile.ZipFile(archive) as bundle:
        assert "capture_quality.json" in bundle.namelist()
        quality = json.loads(bundle.read("capture_quality.json"))
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


def test_export_formats(tmp_path: Path):
    engine = ExportEngine()
    rows = [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]

    json_path = tmp_path / "out.json"
    csv_path = tmp_path / "out.csv"
    xml_path = tmp_path / "out.xml"
    txt_path = tmp_path / "out.txt"

    engine.write_json(json_path, rows)
    engine.write_csv(csv_path, rows)
    engine.write_xml(xml_path, "root", "rows", rows)
    engine.write_txt(txt_path, "title", ["line1", "line2"])

    assert json_path.exists() and json_path.read_text(encoding="utf-8").startswith("[")
    assert csv_path.exists() and "a,b" in csv_path.read_text(encoding="utf-8")
    assert txt_path.exists() and "line1" in txt_path.read_text(encoding="utf-8")

    tree = ET.parse(xml_path)
    root = tree.getroot()
    assert root.tag == "root"
    assert root.find("rows") is not None


def test_session_exports_all_formats_and_raw_commands(tmp_path: Path):
    from dataclasses import replace
    from backend.app.config import settings
    from backend.app.services.diagnostics import DiagnosticSessionService
    from backend.app.store import SqliteStore

    store = SqliteStore(str(tmp_path / "test.db"), 100)
    service = DiagnosticSessionService(replace(settings, output_dir=tmp_path), store)
    files = service._emit_category_files(tmp_path / "system", "system", {"hostname": "test"}, [{"hostname": "test"}])
    assert {item.format for item in files} == {"json", "csv", "xml", "txt"}
    assert ET.parse(tmp_path / "system" / "system.xml").find("rows/item/hostname").text == "test"
    commands = [{"command": "ip route", "exit_code": 0, "stdout": "default via 192.0.2.1", "stderr": ""}]
    files = service._emit_command_files(tmp_path / "commands", commands)
    assert len(files) == 5
    assert "default via 192.0.2.1" in (tmp_path / "commands" / "command_001.txt").read_text(encoding="utf-8")
