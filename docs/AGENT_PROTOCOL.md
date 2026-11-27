# IRQLENS Agent Protocol

This document describes the implemented protocol between `agent/main.py` and `backend/app/main.py`.

## Transport
- Protocol: HTTP JSON over server URL configured by agent (`--server`)
- Auth: Optional `Authorization: Bearer <token>`
- Direction: Agent initiates all requests to server

Machines the server connects to over SSH use the same payloads on a different transport; see [Stream Transport](#stream-transport).

## Endpoints
- `POST /api/agent/register`
- `POST /api/agent/heartbeat`
- `POST /api/agent/telemetry`
- `POST /api/agent/diagnostics/poll`
- `POST /api/agent/diagnostics/{job_id}/results`

## Authentication
- Server checks `IRQLENS_AGENT_TOKEN`, falling back to `IRQLENS_API_TOKEN`; both default to the shared `IRQLENS_TOKEN`.
- The agent reads `--token`, then `IRQLENS_AGENT_TOKEN`, then `IRQLENS_TOKEN`.
- If no token is configured, which is the launcher's default, the server accepts requests without auth headers.
- Unauthorized token returns `401`.
- Dashboard/API credentials are separate when both tokens are set. All agents currently share one agent token; it is not a per-SUT identity credential. Use HTTPS and restrict network access.

## Remote Command Diagnostics
Requires agent version 1.1.0 or newer (1.2.0 for the `storage` category). The server persists a job for each remote command snapshot at capture start/stop. The agent's separate diagnostic worker polls every two seconds without blocking its telemetry loop.

Poll with `POST /api/agent/diagnostics/poll`:

```json
{"sut_id": "sut-01"}
```

Response is `{"job": null}` or:

```json
{"job": {"job_id": "server-generated-id", "session_id": "capture-name", "lease_token": "32-character-hex-lease", "categories": ["routes", "sockets", "ethtool"], "command_timeout_seconds": 8.0}}
```

Only `network`, `interfaces`, `routes`, `sockets`, `ethtool`, and `storage` categories are accepted by the agent. Executable/argument lists are built locally from the allowlist and locally discovered interfaces. There is no remote arbitrary-command or shell interface.

Upload to `POST /api/agent/diagnostics/{job_id}/results` with:

```json
{
  "sut_id": "sut-01",
  "lease_token": "00000000000000000000000000000000",
  "results": [{"timestamp": 1786389901.4, "category": "routes", "command": "ip route", "interface": "", "exit_code": 0, "stdout": "default via 192.0.2.1", "stderr": "", "success": true, "duration_ms": 2.0}],
  "error": ""
}
```

The lease in the upload must be the exact server-issued value, not the example above. Results are scoped to the claimed SUT and session. Duplicate uploads with the same completed lease are idempotent; stale leases return `409`. Jobs can be reclaimed after five minutes if an agent crashes or loses connectivity. The worker retries result uploads without rerunning commands while its process remains alive; a restart/reclaim may execute read-only commands again.

Missing tools and unsuccessful commands are exported with `success: false` and make the job `failed`. Successful output is exported under `<session>/commands/<job-id>/` in JSON/CSV/TXT plus individual command text files. Session detail returns a `remote_diagnostics` list, and `/ws` broadcasts `session_diagnostics_ready` after an upload.

Queued/running jobs may outlive the capture timer, particularly with old or disconnected agents. Downloads/reports represent the data available when requested; wait for jobs to finish for complete command evidence. Late results clear report status so the report can be regenerated.

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

Optional field added in agent 1.3.0: `"role": "client"` or `"server"` (agent `--role`, default `client`). It decides the side of the Overview machine map the machine is drawn on. Older agents omit it and are drawn as clients.

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
- Push current system, IRQ, SoftIRQ, interface, and network samples, plus optional softnet, storage, and PCIe data.
- The server stores one payload in a single transaction and then prunes that SUT's history to the retention window.

Only IRQ lines that fired since the previous sample are sent, highest rate first, at most 1024 rows; when nothing fired, a single idle row marks the tick. `cpu_rates` keys are the real CPU ids from the `/proc/interrupts` header.

Optional fields added in agent 1.2.0 (older agents omit them; the server accepts both):

```json
{
  "system": {"irqbalance": "active"},
  "softnet": {
    "available": true,
    "interval_s": 1.0,
    "total": {"processed": 1200, "dropped": 0, "time_squeeze": 2, "cpu_collision": 0},
    "per_cpu": {"3": {"processed": 1200, "dropped": 0, "time_squeeze": 2, "cpu_collision": 0}}
  },
  "storage": [
    {"device": "nvme0n1", "read_iops": 100.0, "write_iops": 50.0, "read_mb_s": 1.0, "write_mb_s": 0.5,
     "avg_read_await_ms": 0.5, "avg_write_await_ms": 2.0, "queue_depth_in_flight": 7.0, "util_pct": 50.0,
     "model": "...", "scheduler": "none", "nr_requests": 1023, "rotational": false, "numa_node": 1,
     "msi_irqs": ["130", "131"], "msi_irq_count": 2}
  ],
  "pcie": [
    {"bdf": "0000:5e:00.0", "kind": "NIC", "vendor": "0x8086", "device": "0x1592", "class": "0x020000", "driver": "ice",
     "interfaces": ["ens1f0"], "numa_node": 0,
     "current_link_speed": "8.0 GT/s PCIe", "current_link_width": 8, "max_link_speed": "16.0 GT/s PCIe", "max_link_width": 16,
     "link_degraded": true, "msi_irqs": ["120", "121"], "msi_irq_count": 2,
     "aer_dev_correctable": 2, "aer_dev_nonfatal": 0, "aer_dev_fatal": 0, "aer_total": 2, "aer_increasing": false}
  ]
}
```

- `system.irqbalance` is `active`, `inactive`, or `unknown`.
- `softnet` values are deltas since the previous sample; `per_cpu` lists only CPUs with a non-zero delta. An empty object means `/proc/net/softnet_stat` was unreadable.
- `storage` is sent every sample. `pcie` is sent about every 10 seconds and is an empty list in between; the server keeps the last snapshot.
- `aer_dev_*` is `null` when the kernel does not expose the counter.

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

## Stream Transport
Used for machines added with `--client`, `--server`, or `POST /api/machines`. The server starts the connection; there is no HTTP and no token on this path, because the SSH login is the authentication.

Start:
1. The server runs `ssh -T -o BatchMode=yes ... -- <host> "python3 -u -c '<loader>'"`. The loader is one fixed line: read a length, read that many bytes from stdin, execute them.
2. The server writes the length and a bootstrap: the sources of `backend/app/collectors/*.py` and `agent/main.py` from its own checkout, an importer that serves them from memory, and a call to `run_stdio(sut_id, role, telemetry_interval)`. Python older than 3.9 exits with a message.

Machine to server, one JSON object per line on stdout:
- `{"type": "register", ...}`: the registration payload above, once.
- `{"type": "telemetry", ...}`: the telemetry payload above, every interval.
- `{"type": "diagnostics_result", "job_id": "...", "lease_token": "...", "results": [...], "error": ""}`: the result upload above.

Server to machine, one JSON object per line on stdin:
- `{"type": "diagnostics", "job": {...}}`: a claimed job, in the poll response format above. The server looks for one every two seconds.

Rules:
- The server stores everything under the id of the address it connected to (`host`, or `host:port` for a port other than 22), whatever `sut_id` the stream carries.
- Timestamps are shifted onto the server's clock by the smallest difference seen between arrival time and sample time on this connection; a jump of more than 30 seconds starts over.
- Lines on stdout that are not JSON objects are ignored (a greeting printed by the login shell). stderr is kept for the connection's error message.
- Closing stdin ends the process on the machine. It also exits when it cannot write to stdout.
- A connection that ends is retried after 5, 10, 30, then every 60 seconds; one that had been working is retried after 5.

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
- `AGENT_VERSION = "1.3.0"`

Compatibility status:
- No explicit protocol negotiation endpoint is implemented.
- Compatibility is currently based on payload-schema alignment between `agent/main.py` and backend pydantic models.
- On the stream transport the server sends its own copy of the agent, so the two always match.
