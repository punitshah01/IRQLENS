from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.main import app


def test_health_endpoint():
    with TestClient(app) as client:
        r = client.get("/api/health")
        assert r.status_code == 200
        data = r.json()
        assert "application" in data
        assert "collector_status" in data


def test_sessions_lifecycle():
    with TestClient(app) as client:
        start = client.post("/api/sessions/start", json={"categories": ["system", "network"]})
        assert start.status_code == 200
        payload = start.json()
        sid = payload["session"]["session_id"]
        assert sid

        files = client.get(f"/api/sessions/{sid}/files")
        assert files.status_code == 200

        stop = client.post(f"/api/sessions/{sid}/stop", json={"reason": "manual"})
        assert stop.status_code == 200


def test_visualization_endpoints():
    with TestClient(app) as client:
        reg = client.post(
            "/api/agent/register",
            json={
                "sut_id": "viz-sut",
                "name": "Viz SUT",
                "address": "10.0.0.9",
                "port": 8443,
                "token_hint": "none",
                "agent_version": "1.0.0",
                "telemetry_interval": 1.0,
                "hostname": "viz-host",
                "os_distribution": "Ubuntu",
                "os_version": "24.04",
                "kernel": "6.8",
                "architecture": "x86_64",
                "cpu_count": 8,
                "cpu_model": "Intel",
                "memory_total_kb": 1024,
                "numa_nodes": 1,
                "uptime_seconds": 10.0,
                "interfaces": ["eth0"],
                "ip_addresses": ["10.0.0.9"],
                "cpu_topology": [
                    {
                        "cpu_id": 0,
                        "socket_id": 0,
                        "core_id": 0,
                        "numa_node": 0,
                        "online": True,
                        "thread_siblings_list": "0-1",
                        "core_siblings_list": "0-7",
                        "cpu_model": "Intel",
                    }
                ],
            },
        )
        assert reg.status_code == 200

        ts = 1_700_000_000.0
        tel = client.post(
            "/api/agent/telemetry",
            json={
                "type": "telemetry",
                "sut_id": "viz-sut",
                "timestamp": ts,
                "system": {
                    "timestamp": ts,
                    "hostname": "viz-host",
                    "kernel": "6.8",
                    "os_distribution": "Ubuntu",
                    "os_version": "24.04",
                    "uptime_seconds": 100,
                    "boot_time_epoch": ts - 100,
                    "loadavg_1m": 1.0,
                    "loadavg_5m": 1.0,
                    "loadavg_15m": 1.0,
                    "cpu_count": 8,
                    "cpu_model": "Intel",
                    "memory_total_kb": 2048,
                    "memory_available_kb": 1024,
                    "numa_nodes": 1,
                    "running_as_root": False,
                    "architecture": "x86_64",
                },
                "irq_rows": [
                    {
                        "timestamp": ts,
                        "sut_ip": "viz-sut",
                        "sut_id": "viz-sut",
                        "irq": "120",
                        "irq_name": "mlx5_comp0",
                        "device": "mlx5",
                        "interrupt_type": "MSI",
                        "affinity_list": "0-3",
                        "numa_node": "0",
                        "nic": "eth0",
                        "queue": "0",
                        "direction": "RX",
                        "source_class": "network",
                        "total_count": 100,
                        "total_rate": 50.0,
                        "cpu_rates": {"0": 20.0, "1": 30.0},
                    }
                ],
                "softirq": {
                    "timestamp": ts,
                    "sut_ip": "viz-sut",
                    "sut_id": "viz-sut",
                    "totals": {"NET_RX": 10},
                    "rates": {"NET_RX": 5.0},
                    "per_cpu_rates": {"0": 2.0, "1": 3.0},
                },
                "network_samples": [
                    {
                        "timestamp": ts,
                        "sut_ip": "viz-sut",
                        "sut_id": "viz-sut",
                        "interface": "eth0",
                        "rx_bytes": 1000,
                        "tx_bytes": 500,
                        "rx_packets": 10,
                        "tx_packets": 5,
                        "rx_errors": 0,
                        "tx_errors": 0,
                        "rx_drops": 0,
                        "tx_drops": 0,
                        "rx_bps": 100.0,
                        "tx_bps": 50.0,
                        "rx_pps": 10.0,
                        "tx_pps": 5.0,
                        "rx_err_ps": 0.0,
                        "tx_err_ps": 0.0,
                        "rx_drop_ps": 0.0,
                        "tx_drop_ps": 0.0,
                    }
                ],
                "interfaces": [
                    {
                        "timestamp": ts,
                        "name": "eth0",
                        "state": "up",
                        "mtu": 1500,
                        "mac": "00:11:22:33:44:55",
                        "speed_mbps": 10000,
                        "duplex": "full",
                        "driver": "ixgbe",
                        "firmware": "N/A",
                        "ipv4": ["10.0.0.9/24"],
                        "ipv6": [],
                    }
                ],
                "irq_summary": {"total_irq_per_sec": 50.0},
                "network_global": {"rx_bps": 100.0, "tx_bps": 50.0},
                "cpu_topology": [
                    {
                        "cpu_id": 0,
                        "socket_id": 0,
                        "core_id": 0,
                        "numa_node": 0,
                        "online": True,
                        "thread_siblings_list": "0-1",
                        "core_siblings_list": "0-7",
                        "cpu_model": "Intel",
                    }
                ],
            },
        )
        assert tel.status_code == 200

        viz = client.get(f"/api/systems/viz-sut/visualization?from_ts={ts - 1}&to_ts={ts + 1}&top_n=10")
        assert viz.status_code == 200
        payload = viz.json()
        assert payload["sut_id"] == "viz-sut"
        assert "series" in payload
        assert "irq_heatmap" in payload
        assert "cpu_heatmap" in payload
        assert payload["series"]["irq"]

        topo = client.get("/api/systems/viz-sut/visualization/topology")
        assert topo.status_code == 200
        topo_payload = topo.json()
        assert topo_payload["available"] is True
        assert topo_payload["rows"]

        cmp = client.get("/api/visualization/compare?a=viz-sut&b=viz-sut")
        assert cmp.status_code == 200
        assert "deltas" in cmp.json()
