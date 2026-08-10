from __future__ import annotations

import time

from fastapi.testclient import TestClient

from backend.app.main import app


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

        irq = client.get("/api/systems/sut-01/irq")
        assert irq.status_code == 200
        rows = irq.json().get("rows", [])
        assert rows
        assert rows[0]["sut_ip"] == "sut-01"

        net = client.get("/api/network/current?sut_id=sut-01")
        assert net.status_code == 200
        assert net.json().get("host") == "sut-01"
