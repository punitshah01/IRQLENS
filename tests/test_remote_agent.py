from __future__ import annotations

import time

from fastapi.testclient import TestClient

from backend.app.main import app


def test_legacy_and_validation_clients_send_tokens(monkeypatch):
    from unittest.mock import MagicMock
    from collector.irq_collector import post_json
    from tools.validate_sut_visualization import _http_get_json

    monkeypatch.setenv("IRQLENS_AGENT_TOKEN", "test-agent")
    monkeypatch.setenv("IRQLENS_API_TOKEN", "test-api")
    opener = MagicMock()
    monkeypatch.setattr("collector.irq_collector._OPENER", opener)
    post_json("http://localhost/api/irq/ingest", {})
    assert opener.open.call_args.args[0].get_header("Authorization") == "Bearer test-agent"
    fetch = MagicMock()
    fetch.return_value.__enter__.return_value.read.return_value = b'{}'
    monkeypatch.setattr("tools.validate_sut_visualization.urllib.request.urlopen", fetch)
    assert _http_get_json("http://localhost", "/api/health") == {}
    assert fetch.call_args.args[0].get_header("Authorization") == "Bearer test-api"


def test_diagnostic_jobs_persist_and_reclaim(tmp_path, monkeypatch):
    from backend.app.store import SqliteStore

    store = SqliteStore(str(tmp_path / "jobs.db"), 100)
    clock = [1000.0]
    monkeypatch.setattr("backend.app.store.time.time", lambda: clock[0])
    job_id = store.enqueue_diagnostics("capture", "sut-a", ["routes"])
    store = SqliteStore(str(tmp_path / "jobs.db"), 100)
    assert store.claim_diagnostics("sut-b") is None
    first = store.claim_diagnostics("sut-a")
    assert first["job_id"] == job_id
    assert first["categories"] == ["routes"]
    assert store.claim_diagnostics("sut-a") is None
    clock[0] += 301
    retry = store.claim_diagnostics("sut-a")
    assert retry["lease_token"] != first["lease_token"]
    assert not store.finish_diagnostics(job_id, first["lease_token"])
    assert store.finish_diagnostics(job_id, retry["lease_token"])
    assert store.diagnostic_jobs("capture")[0]["status"] == "completed"
    assert store.claim_diagnostics("sut-a") is None


def _sample_registration(sut_id: str = "sut-01") -> dict:
    now = time.time()
    return {
        "sut_id": sut_id,
        "name": "SUT-01",
        "address": "10.0.0.10",
        "port": 8443,
        "token_hint": "configured",
        "agent_version": "1.0.0",
        "telemetry_interval": 1.0,
        "hostname": "sut-host",
        "os_distribution": "Ubuntu",
        "os_version": "24.04",
        "kernel": "6.8.0",
        "architecture": "x86_64",
        "cpu_count": 64,
        "cpu_model": "Intel Xeon",
        "memory_total_kb": 1024 * 1024,
        "numa_nodes": 2,
        "uptime_seconds": 1234.0,
        "interfaces": ["lo", "ens1"],
        "ip_addresses": ["10.0.0.10"],
    }


def _sample_telemetry(sut_id: str = "sut-01") -> dict:
    ts = time.time()
    return {
        "type": "telemetry",
        "sut_id": sut_id,
        "timestamp": ts,
        "system": {
            "timestamp": ts,
            "hostname": "sut-host",
            "kernel": "6.8.0",
            "os_distribution": "Ubuntu",
            "os_version": "24.04",
            "uptime_seconds": 100,
            "boot_time_epoch": ts - 100,
            "loadavg_1m": 1.0,
            "loadavg_5m": 1.0,
            "loadavg_15m": 1.0,
            "cpu_count": 64,
            "cpu_model": "Intel Xeon",
            "memory_total_kb": 2048,
            "memory_available_kb": 1024,
            "numa_nodes": 2,
            "running_as_root": True,
            "architecture": "x86_64",
        },
        "irq_rows": [
            {
                "timestamp": ts,
                "sut_ip": sut_id,
                "sut_id": sut_id,
                "irq": "16",
                "irq_name": "ens1-rx-0",
                "device": "ens1",
                "interrupt_type": "MSI/MSI-X",
                "affinity_list": "0-3",
                "numa_node": "0",
                "nic": "ens1",
                "queue": "0",
                "direction": "RX",
                "source_class": "network",
                "total_count": 100,
                "total_rate": 50.0,
                "cpu_rates": {"0": 50.0},
            }
        ],
        "softirq": {
            "timestamp": ts,
            "sut_ip": sut_id,
            "sut_id": sut_id,
            "totals": {"NET_RX": 10},
            "rates": {"NET_RX": 5.0},
            "per_cpu_rates": {"0": 5.0},
        },
        "network_samples": [
            {
                "timestamp": ts,
                "sut_ip": sut_id,
                "sut_id": sut_id,
                "interface": "ens1",
                "rx_bytes": 1000,
                "tx_bytes": 2000,
                "rx_packets": 10,
                "tx_packets": 20,
                "rx_errors": 0,
                "tx_errors": 0,
                "rx_drops": 0,
                "tx_drops": 0,
                "rx_bps": 100.0,
                "tx_bps": 200.0,
                "rx_pps": 10.0,
                "tx_pps": 20.0,
                "rx_err_ps": 0.0,
                "tx_err_ps": 0.0,
                "rx_drop_ps": 0.0,
                "tx_drop_ps": 0.0,
            }
        ],
        "interfaces": [
            {
                "timestamp": ts,
                "name": "ens1",
                "state": "up",
                "mtu": 1500,
                "mac": "aa:bb:cc:dd:ee:ff",
                "speed_mbps": 10000,
                "duplex": "full",
                "driver": "ixgbe",
                "firmware": "N/A",
                "ipv4": ["10.0.0.10/24"],
                "ipv6": [],
            }
        ],
        "irq_summary": {"total_irq_per_sec": 50.0},
        "network_global": {"rx_bps": 100.0, "tx_bps": 200.0},
    }


def test_agent_register_heartbeat_and_routing():
    with TestClient(app) as client:
        reg = client.post("/api/agent/register", json=_sample_registration("sut-01"))
        assert reg.status_code == 200

        hb = client.post("/api/agent/heartbeat", json={
            "sut_id": "sut-01",
            "agent_version": "1.0.0",
            "uptime_seconds": 200.0,
            "timestamp": time.time(),
        })
        assert hb.status_code == 200

        tel = client.post("/api/agent/telemetry", json=_sample_telemetry("sut-01"))
        assert tel.status_code == 200

        systems = client.get("/api/systems")
        assert systems.status_code == 200
        ids = [s["id"] for s in systems.json().get("systems", [])]
        assert "sut-01" in ids
        registered = next(system for system in systems.json()["systems"] if system["id"] == "sut-01")
        assert registered["sampling_interval_seconds"] == 1.0

        irq = client.get("/api/systems/sut-01/irq")
        assert irq.status_code == 200
        rows = irq.json().get("rows", [])
        assert rows
        assert rows[0]["sut_ip"] == "sut-01"

        net = client.get("/api/network/current?sut_id=sut-01")
        assert net.status_code == 200
        assert net.json().get("host") == "sut-01"


def test_remote_diagnostics_round_trip(monkeypatch):
    from backend.app.main import DIAGNOSTICS, settings
    from backend.app.models import DiagnosticCommandResult
    from agent.main import _collect_diagnostic_job

    monkeypatch.setattr(settings, "agent_token", "test-agent")
    monkeypatch.setattr(settings, "api_token", "test-dashboard")
    agent_headers = {"Authorization": "Bearer test-agent"}
    dashboard_headers = {"Authorization": "Bearer test-dashboard"}
    session_id = f"remote_commands_{time.time_ns()}"
    client = TestClient(app)
    assert client.post("/api/agent/register", json=_sample_registration("diagnostic-sut"), headers=agent_headers).status_code == 200
    DIAGNOSTICS.start(session_id, ["routes"], "sut-host", "Linux", "test", "diagnostic-sut")
    DIAGNOSTICS.collect_snapshot(session_id, ["routes"], "diagnostic-sut")
    assert client.post("/api/agent/diagnostics/poll", json={"sut_id": "diagnostic-sut"}).status_code == 401
    job = client.post("/api/agent/diagnostics/poll", json={"sut_id": "diagnostic-sut"}, headers=agent_headers).json()["job"]
    seen = {}

    def collect_commands(self, interfaces, categories):
        seen["categories"] = categories
        return [DiagnosticCommandResult(timestamp=time.time(), category="routes", command="ip route", exit_code=0,
            stdout="default via 192.0.2.1", stderr="", success=True, duration_ms=1)]

    monkeypatch.setattr("agent.main.DiagnosticCommandCollector.collect", collect_commands)
    payload = _collect_diagnostic_job("diagnostic-sut", {**job, "categories": ["routes", "arbitrary-shell"]})
    assert seen["categories"] == ["routes"]
    endpoint = f"/api/agent/diagnostics/{job['job_id']}/results"
    wrong_sut = client.post(endpoint, json={**payload, "sut_id": "another-sut"}, headers=agent_headers)
    assert wrong_sut.status_code == 404
    assert client.post(endpoint, json=payload, headers=agent_headers).status_code == 200
    assert client.post(endpoint, json=payload, headers=agent_headers).status_code == 200
    detail = client.get(f"/api/sessions/{session_id}", headers=dashboard_headers).json()
    assert detail["remote_diagnostics"][0]["status"] == "completed"
    files = client.get(f"/api/sessions/{session_id}/files", headers=dashboard_headers).json()["files"]
    assert {item["format"] for item in files} == {"json", "csv", "xml", "txt"}
    raw = next(item for item in files if item["name"] == "command_001.txt")
    download = client.get("/api/files", params={"path": raw["path"]}, headers=dashboard_headers)
    assert "default via 192.0.2.1" in download.text
    assert client.delete(f"/api/sessions/{session_id}", headers=dashboard_headers).status_code == 200
