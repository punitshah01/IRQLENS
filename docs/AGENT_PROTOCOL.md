# IRQLENS Agent Protocol

This document describes the implemented protocol between `agent/main.py` and `backend/app/main.py`.

## Transport
- Protocol: HTTP JSON over server URL configured by agent (`--server`)
- Auth: Optional `Authorization: Bearer <token>`
- Direction: Agent initiates all requests to server

## Endpoints
- `POST /api/agent/register`
- `POST /api/agent/heartbeat`
- `POST /api/agent/telemetry`

## Authentication
- Server checks bearer token only when `IRQLENS_AGENT_TOKEN` is non-empty.
- If token is empty, server accepts requests without auth header.
- Unauthorized token returns `401`.

## Registration
Endpoint: `POST /api/agent/register`

Purpose:
- Register SUT identity and baseline capabilities.
- Create/update row in `systems` table.

Example payload (sanitized):
```json
{
  "sut_id": "sut-01",
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
  "memory_total_kb": 2097152,
  "numa_nodes": 2,
  "uptime_seconds": 12345.0,
  "interfaces": ["lo", "ens1"],
  "ip_addresses": ["10.0.0.10"],
  "cpu_topology": [
    {
      "cpu_id": 0,
      "socket_id": 0,
      "core_id": 0,
      "numa_node": 0,
      "online": true,
      "thread_siblings_list": "0-1",
      "core_siblings_list": "0-31",
      "cpu_model": "Intel Xeon"
    }
  ]
}
```

Response fields:
- `ok`
- `sut_id`
- `heartbeat_interval`
- `stale_threshold`

## Heartbeat
Endpoint: `POST /api/agent/heartbeat`

Purpose:
- Liveness updates between telemetry pushes.

Example payload:
```json
{
  "sut_id": "sut-01",
  "agent_version": "1.0.0",
  "uptime_seconds": 13000.5,
  "timestamp": 1786389900.12
}
```

## Telemetry
Endpoint: `POST /api/agent/telemetry`

Purpose:
- Push current system, IRQ, SoftIRQ, interface, and network samples.

Example payload (abridged):
```json
{
  "type": "telemetry",
  "sut_id": "sut-01",
  "timestamp": 1786389901.40,
  "system": {
    "timestamp": 1786389901.40,
    "hostname": "sut-host",
    "kernel": "6.8.0",
    "os_distribution": "Ubuntu",
    "os_version": "24.04",
    "uptime_seconds": 13001.0,
    "boot_time_epoch": 1786376900.4,
    "loadavg_1m": 1.2,
    "loadavg_5m": 1.1,
    "loadavg_15m": 1.0,
    "cpu_count": 64,
    "cpu_model": "Intel Xeon",
    "memory_total_kb": 2097152,
    "memory_available_kb": 1200000,
    "numa_nodes": 2,
    "running_as_root": true,
    "architecture": "x86_64"
  },
  "irq_rows": [
    {
      "timestamp": 1786389901.40,
      "sut_ip": "sut-01",
      "sut_id": "sut-01",
      "irq": "120",
      "irq_name": "ens1-rx-0",
      "device": "ens1",
      "interrupt_type": "MSI/MSI-X",
      "affinity_list": "0-3",
      "numa_node": "0",
      "nic": "ens1",
      "queue": "0",
      "direction": "RX",
      "source_class": "network",
      "total_count": 1000,
      "total_rate": 220.0,
      "cpu_rates": {"0": 120.0, "1": 100.0}
    }
  ],
  "softirq": {
    "timestamp": 1786389901.40,
    "sut_ip": "sut-01",
    "sut_id": "sut-01",
    "totals": {"NET_RX": 20000},
    "rates": {"NET_RX": 180.0, "NET_TX": 30.0},
    "per_cpu_rates": {"0": 95.0, "1": 85.0}
  },
  "network_samples": [
    {
      "timestamp": 1786389901.40,
      "sut_ip": "sut-01",
      "sut_id": "sut-01",
      "interface": "ens1",
      "rx_bytes": 1000000,
      "tx_bytes": 900000,
      "rx_packets": 10000,
      "tx_packets": 9500,
      "rx_errors": 0,
      "tx_errors": 0,
      "rx_drops": 0,
      "tx_drops": 0,
      "rx_bps": 2500000.0,
      "tx_bps": 2200000.0,
      "rx_pps": 12000.0,
      "tx_pps": 11000.0,
      "rx_err_ps": 0.0,
      "tx_err_ps": 0.0,
      "rx_drop_ps": 0.0,
      "tx_drop_ps": 0.0
    }
  ],
  "interfaces": [
    {
      "timestamp": 1786389901.40,
      "name": "ens1",
      "state": "up",
      "mtu": 1500,
      "mac": "aa:bb:cc:dd:ee:ff",
      "speed_mbps": 10000,
      "duplex": "full",
      "driver": "ixgbe",
      "firmware": "N/A",
      "ipv4": [],
      "ipv6": []
    }
  ],
  "irq_summary": {
    "total_irq_per_sec": 220.0,
    "total_softirq_per_sec": 210.0,
    "active_irq_lines": 1,
    "network_related_irqs": 1
  },
  "network_global": {
    "rx_bps": 2500000.0,
    "tx_bps": 2200000.0
  },
  "cpu_topology": []
}
```

## Reconnect Behavior
Agent reconnect strategy (`agent/main.py`):
- On startup, registration retries every 2 seconds until success.
- During runtime, telemetry loop continues and logs HTTP/connection errors.
- Heartbeat is sent at configured heartbeat interval.

## Disconnect and Staleness
Server marks SUT status based on time since last seen:
- `ONLINE`
- `STALE` when age exceeds `IRQLENS_AGENT_STALE_THRESHOLD`
- `OFFLINE` when age exceeds roughly 3x stale threshold

## Error Handling
- `401` on auth failure
- Other HTTP errors printed by agent
- Unexpected runtime exceptions logged by agent and retried next cycle

## Version Compatibility
Current in-repo agent version string:
- `AGENT_VERSION = "1.0.0"`

Compatibility status:
- No explicit protocol negotiation endpoint is implemented.
- Compatibility is currently based on payload-schema alignment between `agent/main.py` and backend pydantic models.
