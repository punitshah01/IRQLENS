from __future__ import annotations

import time

from fastapi.testclient import TestClient

from backend.app.main import app


def test_validation_client_sends_token(monkeypatch):
    from unittest.mock import MagicMock
    from tools.validate_sut_visualization import _http_get_json

    monkeypatch.setenv("IRQLENS_API_TOKEN", "test-api")
    fetch = MagicMock()
    fetch.return_value.__enter__.return_value.read.return_value = b'{}'
    monkeypatch.setattr("tools.validate_sut_visualization.urllib.request.urlopen", fetch)
    assert _http_get_json("http://localhost", "/api/health") == {}
    assert fetch.call_args.args[0].get_header("Authorization") == "Bearer test-api"
    monkeypatch.delenv("IRQLENS_API_TOKEN")
    monkeypatch.setenv("IRQLENS_TOKEN", "test-shared")
    assert _http_get_json("http://localhost", "/api/health") == {}
    assert fetch.call_args.args[0].get_header("Authorization") == "Bearer test-shared"


def test_single_token_and_clone_and_run_defaults(monkeypatch):
    from backend.app.config import REPO_ROOT, Settings

    for name in ("IRQLENS_HOST", "IRQLENS_OUTPUT_DIR", "IRQLENS_RETENTION_MINUTES", "IRQLENS_API_TOKEN", "IRQLENS_AGENT_TOKEN", "IRQLENS_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    defaults = Settings.from_env()
    assert defaults.bind_host == "127.0.0.1" and defaults.bind_port == 10000
    assert defaults.output_dir == (REPO_ROOT / "captures").resolve()
    assert defaults.retention_minutes == 30.0
    assert defaults.api_token == "" and defaults.agent_token == ""
    for removed in ("metric_retention", "retention_days", "max_sessions", "max_storage_mb", "allowed_ingest_ips"):
        assert not hasattr(defaults, removed)

    monkeypatch.setenv("IRQLENS_TOKEN", "shared")
    monkeypatch.setenv("IRQLENS_RETENTION_MINUTES", "5")
    shared = Settings.from_env()
    assert shared.api_token == shared.agent_token == "shared"
    assert shared.retention_minutes == 5.0
    monkeypatch.setenv("IRQLENS_AGENT_TOKEN", "agents-only")
    assert Settings.from_env().agent_token == "agents-only"
    assert Settings.from_env().api_token == "shared"


def test_launcher_serves_all_interfaces_without_sign_in_by_default(monkeypatch, capsys):
    import sys
    from pathlib import Path

    import run_irqlens

    started = []

    class FakeServer:
        def __init__(self, command, **kwargs):
            started.append((command, kwargs["env"]))

        def wait(self):
            return 0

        def poll(self):
            return 0

    monkeypatch.setattr(run_irqlens, "_venv_python", lambda backend: Path(sys.executable))
    monkeypatch.setattr(run_irqlens, "_dependencies_installed", lambda venv_py: True)
    monkeypatch.setattr(run_irqlens, "_detect_host_ip", lambda: "192.0.2.10")
    monkeypatch.setattr(run_irqlens.subprocess, "Popen", FakeServer)
    for name in ("IRQLENS_TOKEN", "IRQLENS_API_TOKEN", "IRQLENS_AGENT_TOKEN"):
        monkeypatch.delenv(name, raising=False)

    def launch(*args):
        monkeypatch.setattr(sys, "argv", ["run_irqlens.py", *args])
        assert run_irqlens.main() == 0
        command, env = started[-1]
        return capsys.readouterr().out, command, env

    out, command, env = launch()
    assert command[-4:] == ["--host", "0.0.0.0", "--port", "10000"]
    assert "http://192.0.2.10:10000" in out and "without sign-in" in out and "Paste the URL" in out
    assert "IRQLENS_TOKEN" not in env and "Generated token" not in out
    box = [line for line in out.splitlines() if line.lstrip().startswith(("┌", "│", "└"))]
    assert len(box) == 8 and len({len(line) for line in box}) == 1

    out, command, env = launch("--port", "8080")
    assert command[-1] == "8080" and "http://192.0.2.10:8080" in out

    # --remote used to generate a token; it is still accepted and no longer does.
    out, command, env = launch("--remote")
    assert "IRQLENS_TOKEN" not in env and "http://192.0.2.10:10000" in out and "without sign-in" in out

    out, command, env = launch("--token", "chosen-token")
    assert env["IRQLENS_TOKEN"] == "chosen-token" and "sign-in required" in out and "chosen-token" not in out

    out, command, env = launch("--host", "127.0.0.1")
    assert command[-4:] == ["--host", "127.0.0.1", "--port", "10000"]
    assert "ssh -L 10000:127.0.0.1:10000" in out and "this host only" in out and "Paste the URL" not in out


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


def test_agent_validation_telemetry_round_trip():
    sut = "io-sut"
    registration = _sample_registration(sut)
    registration["cpu_topology"] = [
        {"cpu_id": 0, "socket_id": 0, "core_id": 0, "numa_node": 0},
        {"cpu_id": 1, "socket_id": 1, "core_id": 0, "numa_node": 1},
    ]
    telemetry = _sample_telemetry(sut)
    telemetry["system"]["irqbalance"] = "active"
    telemetry["irq_rows"][0]["numa_node"] = "0"
    telemetry["irq_rows"][0]["cpu_rates"] = {"0": 20.0, "1": 30.0}
    telemetry["softnet"] = {
        "available": True, "interval_s": 1.0,
        "total": {"processed": 100, "dropped": 4, "time_squeeze": 2, "cpu_collision": 0},
        "per_cpu": {"1": {"processed": 100, "dropped": 4, "time_squeeze": 2, "cpu_collision": 0}},
    }
    telemetry["storage"] = [
        {"device": "nvme0n1", "read_iops": 100.0, "write_iops": 50.0, "read_mb_s": 1.0, "write_mb_s": 0.5,
         "avg_read_await_ms": 0.5, "avg_write_await_ms": 2.0, "queue_depth_in_flight": 7, "util_pct": 50.0, "scheduler": "none"},
        {"device": "sda", "read_iops": 10.0, "write_iops": 0.0, "read_mb_s": 0.25, "write_mb_s": 0.0,
         "avg_read_await_ms": 9.0, "avg_write_await_ms": 0.0, "queue_depth_in_flight": 0, "util_pct": 1.0},
    ]
    telemetry["pcie"] = [
        {"bdf": "0000:5e:00.0", "kind": "NIC", "link_degraded": True, "aer_total": 3, "aer_increasing": True},
        {"bdf": "0000:3b:00.0", "kind": "NVMe", "link_degraded": False, "aer_total": 0, "aer_increasing": False},
    ]
    with TestClient(app) as client:
        assert client.post("/api/agent/register", json=registration).status_code == 200
        assert client.post("/api/agent/telemetry", json=telemetry).status_code == 200

        io = client.get(f"/api/systems/{sut}/io?window_seconds=60")
        assert io.status_code == 200
        payload = io.json()
        assert payload["irqbalance"] == "active"
        assert payload["storage"]["totals"] == {
            "read_iops": 110.0, "write_iops": 50.0, "total_iops": 160.0,
            "read_mb_s": 1.25, "write_mb_s": 0.5, "max_await_ms": 9.0,
        }
        assert sorted(payload["storage"]["series"]) == ["nvme0n1", "sda"]
        assert payload["storage"]["series"]["nvme0n1"][0]["queue_depth_in_flight"] == 7.0
        assert payload["pcie"]["link_warnings"] == 1 and payload["pcie"]["aer_errors"] == 3 and payload["pcie"]["aer_increasing"] == 1
        assert payload["softnet"]["latest"]["total"]["dropped"] == 4
        assert payload["softnet"]["series"][0]["time_squeeze"] == 2.0
        assert client.get("/api/systems/missing-sut/io").status_code == 404

        row = client.get(f"/api/irq/current?sut_id={sut}").json()["rows"][0]
        assert row["cross_numa_flag"] is True
        assert row["handler_numa_nodes"] == [0, 1] and row["remote_share_pct"] == 60.0
        viz = client.get(f"/api/systems/{sut}/visualization?window_seconds=60").json()
        assert viz["irqbalance"] == "active"
        assert [item["irq"] for item in viz["irq_heatmap"]["irqs"]] == ["16"]


def test_dashboard_responses_are_compact_compressed_and_revalidated():
    sut = "compact-sut"
    cpus = [str(cpu) for cpu in range(64)]
    telemetry = _sample_telemetry(sut)
    telemetry["cpu_utilization"] = {cpu: float(cpu) for cpu in cpus}
    telemetry["irq_rows"][0]["cpu_rates"] = {cpu: 1.0 for cpu in cpus}
    telemetry["softirq"]["per_cpu_rates"] = {cpu: 2.0 for cpu in cpus}
    with TestClient(app) as client:
        assert client.post("/api/agent/register", json=_sample_registration(sut)).status_code == 200
        assert client.post("/api/agent/telemetry", json=telemetry).status_code == 200
        url = f"/api/systems/{sut}/visualization?window_seconds=60"
        full = client.get(url)
        compact = client.get(url + "&compact=1")
        plain = client.get(url, headers={"Accept-Encoding": "identity"})
        index = client.get("/")
        script = client.get("/frontend/app.js")
        unchanged = client.get("/frontend/app.js", headers={"If-None-Match": script.headers["etag"]})

    # The default response keeps every per-CPU field.
    data = full.json()
    assert data["series"]["cpu_utilization"][0]["cpus"]["63"] == 63.0
    assert len(data["irq_heatmap"]["values"]) == 64 and len(data["cpu_heatmap"]["values"]) == 64
    assert data["series"]["softirq_classes"]["NET_RX"][0][1] == 5.0

    # compact=1 drops the per-CPU history and cells and the softirq history, nothing else.
    lean = compact.json()
    point = lean["series"]["cpu_utilization"][0]
    assert sorted(point) == ["avg", "max", "timestamp"] and point["avg"] == 31.5 and point["max"] == 63.0
    assert "values" not in lean["irq_heatmap"] and "values" not in lean["cpu_heatmap"]
    assert lean["cpu_utilization"] == data["cpu_utilization"] and len(lean["cpu_utilization"]) == 64
    for key in ("irq_heatmap", "cpu_heatmap"):
        assert lean[key] == {name: value for name, value in data[key].items() if name != "values"}
    assert sorted(lean["series"]) == ["cpu_utilization", "irq", "network"]
    assert lean["series"]["irq"] == data["series"]["irq"] and lean["series"]["network"] == data["series"]["network"]
    for key in ("stats", "top_irq_sources", "irq_distribution", "network_interfaces", "health", "irqbalance"):
        assert lean[key] == data[key]
    assert data["stats"]["softirq"]["current"] == 5.0
    assert len(compact.content) < len(full.content) / 2

    assert full.headers["content-encoding"] == "gzip" and "Accept-Encoding" in full.headers["vary"]
    assert "content-encoding" not in plain.headers and plain.json()["series"] == data["series"]

    # A reload must ask the server again instead of running a cached dashboard.
    assert index.headers["cache-control"] == "no-cache"
    assert script.headers["cache-control"] == "no-cache" and script.headers["content-encoding"] == "gzip"
    assert "refreshSelectedHistory" in script.text
    assert unchanged.status_code == 304 and unchanged.headers["cache-control"] == "no-cache"


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
    assert {item["format"] for item in files} == {"json", "csv", "txt"}
    raw = next(item for item in files if item["name"] == "001_ip_route.txt")
    download = client.get("/api/files", params={"path": raw["path"]}, headers=dashboard_headers)
    assert "default via 192.0.2.1" in download.text
    assert client.delete(f"/api/sessions/{session_id}", headers=dashboard_headers).status_code == 200
