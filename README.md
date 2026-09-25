# IRQLENS

Linux IRQ, SoftIRQ, CPU, network, and system performance monitoring and diagnostics dashboard.

[![Python](https://img.shields.io/badge/Python-Repository%20does%20not%20pin%20minor%20version-2f7ed8)](#requirements) [![Tests](https://img.shields.io/badge/Tests-pytest%20%28local%20command%29-2f7ed8)](#testing) [![License](https://img.shields.io/badge/License-Not%20specified-b76e00)](#license) [![Status](https://img.shields.io/badge/Status-Active%20development-2f7ed8)](#overview)

## Table of Contents
- [Overview](#overview)
- [What IRQLENS Can Do](#what-irqlens-can-do)
- [Architecture](#architecture)
- [Local and Remote Modes](#local-and-remote-modes)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Start IRQLENS Server](#start-irqlens-server)
- [Install IRQLENS Agent on SUT](#install-irqlens-agent-on-sut)
- [Dashboard Pages](#dashboard-pages)
- [Visualizations](#visualizations)
- [Linux Data Sources](#linux-data-sources)
- [IRQ Rate Calculations](#irq-rate-calculations)
- [Network Rate Calculations](#network-rate-calculations)
- [Data Storage](#data-storage)
- [Diagnostic Collection](#diagnostic-collection)
- [Output Directory Structure](#output-directory-structure)
- [Security](#security)
- [Network and Firewall Requirements](#network-and-firewall-requirements)
- [API and WebSocket](#api-and-websocket)
- [Configuration Reference](#configuration-reference)
- [Testing](#testing)
- [Validation](#validation)
- [Troubleshooting](#troubleshooting)
- [Repository Structure](#repository-structure)
- [Additional Documentation](#additional-documentation)
- [Contributing](#contributing)
- [License](#license)

## Overview
IRQLENS is a Linux observability tool focused on interrupt and network behavior. It exposes telemetry through FastAPI and WebSocket, stores history in SQLite, renders dashboards in the browser, and can collect exportable diagnostic snapshots.

It supports:
- Local Linux monitoring (server-side collectors read local /proc and /sys)
- Remote Linux SUT monitoring through the IRQLENS agent
- Multi-SUT registration and selection
- Live telemetry updates through WebSocket to the browser

## What IRQLENS Can Do
Implemented capabilities in this repository:
- Monitor local Linux systems
- Monitor remote Linux SUTs through the agent
- Visualize IRQ activity and trends
- Visualize IRQ-to-CPU distribution
- Visualize SoftIRQ activity and trends
- Visualize network throughput/errors/drops and interface-level metrics
- Detect and display network interfaces
- Visualize CPU and NUMA topology when available
- Collect system/network diagnostic snapshots
- Save raw command outputs
- Export JSON, CSV, XML, and TXT session artifacts
- Support multiple SUTs
- Push live UI refresh events through WebSocket

## Architecture

```text
                    +---------------------+
                    |      Browser        |
                    |   IRQLENS Dashboard |
                    +----------+----------+
                               |
                         HTTP/WebSocket
                               |
                               v
                    +---------------------+
                    |   IRQLENS Server    |
                    |                     |
                    | FastAPI API         |
                    | WebSocket hub       |
                    | Visualization logic |
                    | SQLite store        |
                    +----------+----------+
                               |
                         Agent Protocol
                               |
              +----------------+----------------+
              |                |                |
              v                v                v
        +-----------+    +-----------+    +-----------+
        |  SUT #1   |    |  SUT #2   |    |  SUT #3   |
        |   Agent   |    |   Agent   |    |   Agent   |
        |  Linux    |    |  Linux    |    |  Linux    |
        +-----------+    +-----------+    +-----------+
```

What runs where:
- Browser: `frontend/index.html` single-page dashboard with ECharts visualizations
- IRQLENS server: `backend/app/main.py` FastAPI app, telemetry sampler, diagnostics APIs, and WebSocket endpoint
- Database: SQLite at `IRQLENS_DB_PATH` (default `backend/data/irqlens.db` when started from `run_irqlens.py`)
- Remote SUT agent: `agent/main.py` reads SUT-local Linux sources and POSTs telemetry to server

## Local and Remote Modes
### Local mode
```text
IRQLENS Server
      |
Local collectors
      |
Local Linux SUT
```

### Remote mode
```text
IRQLENS Server
      |
SUT Agent
      |
Remote Linux SUT
```

Why /proc and /sys must be read on the SUT:
- IRQ counters, SoftIRQ counters, interface counters, and CPU topology are kernel-local runtime views.
- Reading them on a different machine does not reflect the target SUT state.
- IRQLENS remote mode therefore runs data collection on the SUT, then forwards sanitized telemetry to the server.

## Requirements
Repository-grounded requirements:

### Python
- Repository does not declare a strict minor version in metadata.
- Current code is compatible with Python 3.9+.
- This workspace was executed with Python 3.13 (`.venv`), and the reported Linux startup failure was fixed for Python 3.9.

### Linux distributions
- The code is Linux-oriented and reads Linux proc/sys paths.
- Supported Ubuntu versions: Not explicitly version-pinned in repository.
- Supported CentOS/RHEL versions: Not explicitly version-pinned in repository.
- Practical requirement is kernel/userspace exposing the documented proc/sys files.

### Required Linux filesystems and files
- Required for full local collection:
  - `/proc/interrupts`
  - `/proc/softirqs`
  - `/proc/net/dev`
  - `/proc/uptime`
  - `/proc/loadavg`
  - `/proc/cpuinfo`
  - `/proc/meminfo`
  - `/sys/class/net/*`
- Optional/partial enrichment:
  - `/proc/irq/*/smp_affinity_list`
  - `/sys/kernel/irq/*/node`
  - `/sys/devices/system/cpu/*`
  - `/sys/devices/system/node/*`

### Required and optional command-line tools
- Required for core app startup: Python runtime and pip.
- Optional command enrichments (auto-detected): `ip`, `ss`, `ethtool`, `sysctl`, `lscpu`, `numactl`.
- Core telemetry continues if optional tools are missing.

### Ports
- Default server bind: `0.0.0.0:8080`.
- Browser connects to server over HTTP and WebSocket on the same port.
- Agent sends HTTP POSTs to server on server port (default 8080).

### Permissions
- Non-root works for core telemetry in many environments.
- Root may be required for complete IRQ/device metadata depending on kernel/security policy.

### Disk
- IRQ, SoftIRQ, network, interface, and system history is trimmed by row count per SUT (`IRQLENS_METRIC_RETENTION`). CPU utilization, topology, and heartbeat history is not yet trimmed.
- Session artifacts are written under `IRQLENS_OUTPUT_DIR` (default `/root/irqlens`).
- `IRQLENS_RETENTION_DAYS`, `IRQLENS_MAX_SESSIONS`, and `IRQLENS_MAX_STORAGE_MB` are configurable but not currently enforced by cleanup logic.

### Browser
- Modern browser required for ES modules/features and WebSocket.
- ECharts is loaded from CDN (`cdn.jsdelivr.net`).

## Installation

### Option A: repository-root quick bootstrap
```bash
git clone <repository-url>
cd IRQLENS
python3 -m venv .venv
source .venv/bin/activate
python run_irqlens.py
```

### Option B: backend-only explicit setup
```bash
git clone <repository-url>
cd IRQLENS/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

### Windows backend setup
```powershell
git clone <repository-url>
cd IRQLENS\backend
python -m venv .venv
. .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

## Quick Start
1. Clone the repository.
2. Install backend dependencies.
3. Start IRQLENS server.
4. On each Linux SUT, start IRQLENS agent.
5. Set server URL and token on agent.
6. Verify SUT appears as `ONLINE` in `GET /api/systems`.
7. Open dashboard in browser: `http://<server>:8080`.
8. Check the selected SUT hostname in the header. The UI currently restores a saved SUT or automatically chooses a remote SUT, falling back to the local system.
9. Confirm live updates (`Connected` WebSocket status).
10. Review IRQ, SoftIRQ, CPU, and Network visualizations.
11. Start a diagnostics session and download artifacts.

## Start IRQLENS Server
Actual implemented commands:

```bash
# from repo root
python run_irqlens.py
```

or

```bash
# from backend/
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Runtime details:
- Host and port: `IRQLENS_HOST` / `IRQLENS_PORT` (default `0.0.0.0:8080`)
- DB location: `IRQLENS_DB_PATH` (default from `run_irqlens.py`: `backend/data/irqlens.db`)
- Session/log output root: `IRQLENS_OUTPUT_DIR` (default `/root/irqlens`)
- Log level: `IRQLENS_LOG_LEVEL` (default `INFO`)

## Install IRQLENS Agent on SUT
Agent implementation path: `agent/main.py`

```bash
# on Linux SUT
cd IRQLENS
python3 agent/main.py \
  --server http://<irqlens-server>:8080 \
  --sut-id <sut-id> \
  --name <display-name> \
  --token <agent-token> \
  --telemetry-interval 1.0 \
  --heartbeat-interval 5.0
```

Agent configuration options:
- `--server` (or `IRQLENS_AGENT_SERVER`)
- `--sut-id` (or `IRQLENS_AGENT_SUT_ID`)
- `--name` (or `IRQLENS_AGENT_NAME`)
- `--token` (or `IRQLENS_AGENT_TOKEN`)
- `--telemetry-interval` (or `IRQLENS_AGENT_TELEMETRY_INTERVAL`)
- `--heartbeat-interval` (or `IRQLENS_AGENT_HEARTBEAT_INTERVAL`)

Authentication:
- Agent and legacy-ingest endpoints use `IRQLENS_AGENT_TOKEN`, falling back to `IRQLENS_API_TOKEN` when only that token is set.
- Dashboard, other APIs, downloads, and WebSocket use `IRQLENS_API_TOKEN`, falling back to `IRQLENS_AGENT_TOKEN`.
- Both empty means authentication is disabled for trusted, isolated development only. See [Security](#security).

Permissions and host requirement:
- Agent is intended for Linux only.
- Agent reads `/proc` and `/sys` directly on SUT.

How to verify connection:
- The header shows the selected SUT status (`ONLINE`, `STALE`, `OFFLINE`).
- API: `GET /api/systems`.
- UI WebSocket status should show `Connected`.

## Dashboard Pages
The five primary views follow [IRQLENS UI Rules](docs/IRQLENS_UI_RULES.md):

### Overview
Current IRQ/SoftIRQ/network KPIs, IRQ and network trends, CPU/NUMA matrix, and findings for the selected SUT.

### CPU
Topology-based CPU matrix with IRQ, SoftIRQ, and utilization metrics, per-CPU detail, and utilization history.

### IRQ
Ranked interrupt sources, IRQ-to-CPU heatmap, search/filter controls, and IRQ metadata table.

### Network
Interface selection, throughput trends, packet errors/drops, related IRQs, and metadata including IPv4/IPv6 addresses.

### Collect Logs
Named timed captures, category selection, recent capture history, remote-command job status, file downloads, ZIP archives, and HTML reports.

There are no separate Systems, Sessions, Settings, Admin, Logs, or Compare pages. System listing and comparison APIs remain available. Interactive multi-SUT switching is currently not exposed in the UI; the header identifies the automatically selected or previously saved SUT.

## Visualizations
### IRQ heatmap
- X-axis: CPU
- Y-axis: IRQ
- Cell intensity: IRQ/sec for IRQ-CPU pair
- Source: visualization payload `irq_heatmap.values`

### CPU heatmap
- X-axis: CPU
- Y-axis: metric (`IRQ/s`, `SoftIRQ/s`)
- Cell intensity: per-CPU metric rate

### Network trends
- RX/TX history for all interfaces or the selected interface.
- Current packet rates, errors, drops, and interface metadata accompany the trend.

### IRQ/CPU correlation
- Relationship from observed per-IRQ `cpu_rates` and top CPU contributors.
- This is correlation from co-observed rates, not causal proof.

### Network/IRQ correlation
- Relationship inferred by matching IRQ row NIC label (`row.nic`) against network interface name.
- Uses IRQ direction (`RX`, `TX`, `Other`) to aggregate per-interface IRQ rates.
- Correlation only; does not imply causation.

### NUMA topology and distribution
- Topology source: `/sys/devices/system/cpu/*/topology` and CPU-to-node associations.
- For remote systems, topology comes from agent payload snapshots.
- NUMA IRQ chart is aggregated by `irq_rows[].numa_node`.

### Health and anomalies
- IRQ balance: entropy/CV-based score from backend `irq_balance_score`.
- Spike detection: baseline-window ratio from backend `detect_spikes`.
- Overview findings summarize IRQ concentration and packet drops; no global threshold sliders are exposed.

## Linux Data Sources
| Source | Used For | Required |
|---|---|---|
| `/proc/interrupts` | IRQ counts by IRQ line and CPU | Yes (core IRQ telemetry) |
| `/proc/softirqs` | SoftIRQ totals and per-CPU totals | Yes (core SoftIRQ telemetry) |
| `/proc/net/dev` | Interface bytes/packets/errors/drops | Yes (core network telemetry) |
| `/proc/net/snmp` | Validation script health checks | Optional for dashboard telemetry; required for full validator pass |
| `/proc/net/netstat` | Validation script health checks | Optional for dashboard telemetry; required for full validator pass |
| `/proc/net/softnet_stat` | Validation script health checks | Optional for dashboard telemetry; required for full validator pass |
| `/proc/stat` | Legacy local collector CPU percentages | Optional (collector artifacts) |
| `/proc/meminfo` | Total/available memory in system telemetry | Yes for full system metadata |
| `/proc/cpuinfo` | CPU model string | Yes for full system metadata |
| `/proc/uptime` | Uptime and boot-time derivation | Yes for full system metadata |
| `/proc/loadavg` | 1m/5m/15m load averages | Yes for full system metadata |
| `/sys/class/net/` | Interface state, mtu, mac, speed, duplex, driver | Yes for interface metadata |
| `/sys/kernel/irq/` | IRQ NUMA node lookup | Optional enrichment |
| `/sys/devices/system/cpu/` | CPU topology and sibling info | Optional enrichment |
| `/sys/devices/system/node/` | NUMA node count | Optional enrichment |

Command usage:
- `ip`: addresses, links, routes, neighbors. Interface addressing uses `ip -j address show`, cached for 30 seconds; missing/unsupported `ip` leaves address lists empty without stopping telemetry.
- `ss`: socket summaries/listeners
- `ethtool`: NIC details and counters
- `sysctl`: network/system kernel settings snapshot
- `lscpu`, `numactl`: dependency visibility in health checks
- `uname`: kernel/version context via Python `platform` APIs

## IRQ Rate Calculations
Core formula used in collectors:

```text
IRQ/sec = max(0, current_irq_count - previous_irq_count) / elapsed_seconds
```

Handling details:
- Counter reset/wrap handling: if delta < 0, collector treats delta as current value.
- Missing IRQ between samples: no rate emitted for missing IRQ line in that sample.
- New IRQ line: first observation has no prior baseline, so no initial rate until next sample.
- CPU-count changes or mismatched vector lengths: sample is skipped for that IRQ in that cycle.
- Sampling interval:
  - Local backend sampler: `IRQLENS_COLLECTION_INTERVAL` (default 1.0s)
  - Remote agent: `--telemetry-interval` (default 1.0s)

## Network Rate Calculations
Per interface formulas:

```text
RX bytes/sec = max(0, rx_bytes_now - rx_bytes_prev) / elapsed_seconds
TX bytes/sec = max(0, tx_bytes_now - tx_bytes_prev) / elapsed_seconds
RX packets/sec = max(0, rx_packets_now - rx_packets_prev) / elapsed_seconds
TX packets/sec = max(0, tx_packets_now - tx_packets_prev) / elapsed_seconds
RX errors/sec = max(0, rx_errors_now - rx_errors_prev) / elapsed_seconds
TX errors/sec = max(0, tx_errors_now - tx_errors_prev) / elapsed_seconds
RX drops/sec = max(0, rx_drops_now - rx_drops_prev) / elapsed_seconds
TX drops/sec = max(0, tx_drops_now - tx_drops_prev) / elapsed_seconds
```

Value semantics:
- `/proc/net/dev` counters are cumulative kernel counters.
- IRQLENS dashboard rates are sampled deltas over elapsed interval.
- Trend charts show sampled timeseries points; not long-window moving averages.

## Data Storage
SQLite backend (`backend/app/store.py`) stores:
- `irq_samples`: IRQ rows with CPU rate map and metadata
- `softirq_samples`: class rates and per-CPU softirq rates
- `network_samples`: interface network deltas and counters
- `interface_samples`: latest interface metadata snapshots
- `system_samples`: system metadata snapshots
- `cpu_topology_samples`: local or remote topology payload snapshots
- `systems`: registered systems and status
- `agent_heartbeats`: liveness records
- `sessions`: diagnostic session metadata
- `session_files`: exported file catalog
- `diagnostic_jobs`: persisted remote-command requests and lease/status metadata

Retention behavior:
- IRQ, SoftIRQ, network, interface, and system tables are trimmed per SUT to `IRQLENS_METRIC_RETENTION` rows; CPU utilization, topology, and heartbeats currently are not.
- Row retention is not a duration guarantee. Large IRQ/interface counts can truncate history before a capture ends.
- Day/session/storage-limit settings are present in config but currently not actively enforced by cleanup jobs.

## Diagnostic Collection
Implemented flow:

```text
Start Session
      |
Collect selected categories from store/local collectors
      |
Run local allowlisted commands or enqueue a remote agent job
      |
Save raw command output .txt files
      |
Generate JSON/CSV/XML/TXT exports per category
      |
Record files in SQLite and optional ZIP archive on download
```

Categories:
- `irq`, `softirq`, `network`, `interfaces`, `routes`, `sockets`, `ethtool`, `system`

Output formats:
- JSON
- CSV
- XML
- TXT
- ZIP (session archive endpoint)

Remote commands require agent version 1.1.0 or newer. A separate agent worker polls every two seconds and executes only fixed diagnostic categories on the selected SUT. Jobs survive server restarts; an unacknowledged running job can be retried after its five-minute lease expires. No arbitrary command text is accepted from the server.

Capture details show `queued`, `running`, `completed`, or `failed` remote jobs. A timed capture can finish while its commands are still pending; wait for terminal job states before taking a final ZIP/report. Downloads are point-in-time snapshots. Late results invalidate the report status so it can be regenerated. Missing tools, permissions, timeouts, and output limits are preserved in command results, not reported as successful collection.

Command batches have a 120-second budget checked between commands, at most 500 executed commands, and an approximately 4 MiB output budget. Individual streams are capped at 65,536 characters with a truncation warning. Remote per-command timeouts are capped at 30 seconds. IRQ/network time-series capture retains its existing database-retention limitations.

## Output Directory Structure
### Capture Quality
Each completed capture includes `capture_quality.json`, a file inventory with sizes and SHA-256 hashes, and `metadata/capture_config.json` recording the requested duration and sampling interval at capture start. New captures export time-series files only for selected categories. Metadata is refreshed when reports and ZIPs are generated.

In Collect Logs, open a capture and use **Check Evidence** to recalculate its quality. The authenticated endpoint is `POST /api/sessions/{session_id}/quality`; session detail includes the saved result as `capture_quality`. Checks do not regenerate telemetry or move the capture's start/end timestamps. Running captures cannot be manually rechecked.

- `COMPLETE`: all required evidence checks passed. This is not a workload or silicon PASS.
- `INCOMPLETE`: missing/unusable snapshots, unknown sampling configuration, missing files, failed/pending commands, or insufficient stream coverage.
- `CAPTURING`: capture is still running.
- `NOT_EVALUATED`: no saved quality result, typically for an older capture.

Required streams need at least two unique samples, at least 90% of the expected sample count, and no gap larger than 2.5 sampling intervals, including both capture boundaries. Invalid, duplicate, out-of-window, and out-of-order timestamps fail completeness. Per-interface coverage is checked for network captures. CPU frequency is optional. Old captures without recorded sampling configuration remain incomplete; no interval is guessed.

The manifest describes current evidence and provides hashes for downstream integrity checks; rechecking recomputes those hashes, not a comparison against a trusted original. Clock synchronization, counter correctness, IRQ mapping, performance thresholds, and silicon qualification are not evaluated. Existing retention limits can still remove samples; the quality check identifies the resulting missing coverage rather than recovering it.

### Files
Default root: `/root/irqlens`

```text
/root/irqlens/
+-- latest/session.txt
+-- <session-name>/
|   +-- metadata/metadata.json
|   +-- summary.json
|   +-- irq/irqtop.{json,csv,xml,txt}
|   +-- softirq/softirq.{json,csv,xml,txt}
|   +-- network/network.{json,csv,xml,txt}
|   +-- interfaces/interfaces.{json,csv,xml,txt}
|   +-- system/system.{json,csv,xml,txt}
|   +-- commands/commands.{json,csv,xml,txt}  (local)
|   +-- commands/command_001.txt             (local raw output)
|   +-- commands/<job-id>/                   (remote)
|   |   +-- commands.{json,csv,xml,txt}
|   |   +-- command_001.txt
|   |   +-- status.json
|   +-- timeseries/                         (JSON history)
|   +-- report.html                         (generated on request)
+-- <session-name>.zip                      (generated on download)
```

Local legacy collector (`collector/irq_collector.py`) also writes artifacts to `IRQLENS_ARTIFACT_DIR` (default `./artifacts`).

## Security
Current implemented controls:
- Optional dashboard/API token (`IRQLENS_API_TOKEN`) protects pages, telemetry reads, management mutations, session exports, API docs, and WebSocket access.
- Optional agent bearer token (`IRQLENS_AGENT_TOKEN`) protects every `/api/agent/*` endpoint and legacy `/api/irq/ingest`.
- Each token falls back to the other when only one is configured. Configure distinct values to prevent agent credentials from accessing dashboard/management APIs.
- Browser sign-in uses HTTP Basic: username `irqlens`, password equal to the dashboard/API token. Scripts use `Authorization: Bearer <token>`. Credentials are not stored in browser local storage or URL query strings.
- Browser sign-in issues a signed 12-hour HttpOnly, SameSite=Strict session cookie for subsequent requests and WebSockets; it is Secure over HTTPS and contains no API token. Token rotation invalidates existing cookies. Native HTTP Basic credential caching remains browser-controlled.
- Cross-origin mutations and WebSocket connections require the same origin or an explicit `IRQLENS_CORS_ORIGINS` entry; `*` does not grant this permission.
- `/health` is an intentionally unauthenticated, minimal liveness endpoint.
- Optional ingest IP allowlist (`IRQLENS_ALLOWED_INGEST_IPS`) for legacy ingest API
- Command execution is restricted to allowlisted diagnostics in `DiagnosticCommandCollector`
- No arbitrary shell execution API exists
- File download endpoint validates requested paths are under configured output root

Important operational warning:
- Authentication is disabled when both tokens are empty. Set tokens before exposing the server beyond an isolated trusted environment. Restart the server after changing token configuration.
- HTTP Basic and bearer tokens require HTTPS/WSS outside loopback. Use TLS termination, network ACLs, and access restrictions. This is shared-token authentication, not per-user roles or per-SUT credentials.
- A reverse proxy must forward Authorization and WebSocket upgrades and preserve the external scheme/host with trusted proxy configuration.

TLS/WSS note:
- Native HTTPS/WSS termination is not configured in this repository.
- Deploy behind reverse proxy/load balancer for TLS in production.

Sensitive data considerations:
- Diagnostic outputs may include network addressing and system-level details.
- Treat session files as operationally sensitive artifacts.

## Network and Firewall Requirements
Connection direction:
- Browser initiates HTTP and WebSocket connections to IRQLENS server.
- Agent initiates HTTP POST connections to IRQLENS server.

Default flow:

```text
Browser  --->  IRQLENS Server :8080 (HTTP + WS)
Agent    --->  IRQLENS Server :8080 (HTTP POST)
```

The `port` field in system registration defaults to `8443` as metadata for SUT records; current telemetry transport still targets server URL/port configured by agent (`--server`).

## API and WebSocket
OpenAPI and docs:
- `GET /openapi.json`
- `GET /docs`
- `GET /redoc`

Major API groups:
- Health/system routing: `/api/health`, `/api/system`, `/api/systems*`
- Telemetry reads: `/api/irq/*`, `/api/softirq/current`, `/api/network/*`, `/api/interfaces`
- Visualization: `/api/systems/{sut_id}/visualization*`, `/api/visualization/compare`
- Sessions/files: `/api/sessions*`, `/api/files`
- Agent protocol: `/api/agent/register`, `/api/agent/heartbeat`, `/api/agent/telemetry`
- Remote commands: `/api/agent/diagnostics/poll`, `/api/agent/diagnostics/{job_id}/results`
- Legacy ingest compatibility: `/api/irq/ingest`, `/api/irq/latest`, `/api/host/latest`, `/api/summary/current`

WebSocket:
- Browser uses `/ws` for event-driven refresh triggers.
- Agent does not use WebSocket in current implementation; it uses HTTP POST telemetry + heartbeat.

## Configuration Reference
From `backend/app/config.py`. Set environment variables in the launching shell; a `.env` file is not automatically loaded. The root launcher currently binds `0.0.0.0:8080`; use explicit uvicorn `--host`/`--port` flags for another bind address.

| Setting | Description | Default | Required |
|---|---|---|---|
| `IRQLENS_HOST` | Server bind address | `0.0.0.0` | No |
| `IRQLENS_PORT` | Server bind port | `8080` | No |
| `IRQLENS_DB_PATH` | SQLite DB file path | `./data/irqlens.db` | No |
| `IRQLENS_OUTPUT_DIR` | Session/output root | `/root/irqlens` | No |
| `IRQLENS_COLLECTION_INTERVAL` | Local sampler interval (seconds) | `1.0` | No |
| `IRQLENS_SUPPORTED_INTERVALS` | UI interval options | `0.1,0.25,0.5,1,2,5,10` | No |
| `IRQLENS_METRIC_RETENTION` | Per-SUT row retention cap | `5000` | No |
| `IRQLENS_RETENTION_DAYS` | Configured retention days (not enforced yet) | `14` | No |
| `IRQLENS_MAX_SESSIONS` | Configured max sessions (not enforced yet) | `100` | No |
| `IRQLENS_MAX_STORAGE_MB` | Configured storage cap (not enforced yet) | `1024` | No |
| `IRQLENS_COMMAND_TIMEOUT_SECONDS` | Diagnostic command timeout | `8.0` | No |
| `IRQLENS_LOG_LEVEL` | Backend log level | `INFO` | No |
| `IRQLENS_CORS_ORIGINS` | CORS allow list | `*` | No |
| `IRQLENS_ALLOWED_INGEST_IPS` | Legacy ingest allowed client IPs | empty | No |
| `IRQLENS_DISABLE_INGEST_ALLOWLIST` | Disable ingest IP allowlist | `0` | No |
| `IRQLENS_AGENT_TOKEN` | Agent bearer token | empty | No (recommended in remote mode) |
| `IRQLENS_API_TOKEN` | Dashboard/API credential; falls back to agent token | empty | Recommended outside isolated development |
| `IRQLENS_AGENT_HEARTBEAT_INTERVAL` | Suggested heartbeat interval returned to agent | `5` | No |
| `IRQLENS_AGENT_STALE_THRESHOLD` | Stale/offline threshold basis | `15` | No |

## Testing
Backend tests (storage is automatically isolated in a temporary directory):

```bash
python -m pip install -r backend/requirements.txt
python -m pytest tests -q
```

Full suite including Chromium:

```bash
python -m pip install -r requirements-dev.txt
python -m playwright install chromium
python -m pytest tests -q --browser chromium
```

On Linux CI, use `python -m playwright install --with-deps chromium`. Browser tests explicitly skip when their optional dependencies are absent. CI runs backend tests on Linux/Windows with Python 3.9 and 3.13 and browser tests on Linux.

Coverage includes API and WebSocket authentication, origin rejection, legacy-client headers, remote command routing/retry/export, interface address discovery, timeout decoding, session export formats, frontend navigation, untrusted telemetry escaping, and remote failure display. Real Linux driver permissions and live multi-SUT/network-failure scenarios still require SUT validation.

## Validation
Validation tool:
- `tools/validate_sut_visualization.py`

Example (Linux full local source + API checks):

```bash
python3 tools/validate_sut_visualization.py \
  --server-url http://<server>:8080 \
  --sut-id <sut-id> \
  --mode local \
  --sample-interval 1.0
```

Example (non-Linux host validating API payload path only):

```bash
python tools/validate_sut_visualization.py \
  --server-url http://127.0.0.1:8080 \
  --sut-id <sut-id> \
  --mode api-only
```

PASS/WARN/FAIL semantics:
- `PASS`: check succeeded
- `WARN`: non-blocking absence/idle/limited environment condition
- `FAIL`: blocking failure; script exits non-zero

## Troubleshooting
### Agent does not connect
Check:
- Agent server URL (`--server`) and port reachability
- Server is listening on expected interface/port
- Bearer token matches `IRQLENS_AGENT_TOKEN`
- Firewall policy allows agent -> server outbound
- Agent stdout for HTTP 401 or connection errors

### SUT shows OFFLINE or STALE
Check:
- Agent process health on SUT
- Heartbeat POST cadence
- Server stale threshold config (`IRQLENS_AGENT_STALE_THRESHOLD`)

### No IRQ data
Check on SUT:
```bash
cat /proc/interrupts
```

### No network interfaces
Check on SUT:
```bash
ls /sys/class/net
ip link
```

### No network traffic
Check on SUT:
```bash
cat /proc/net/dev
```

### ethtool unavailable
- Diagnostic snapshots will omit ethtool command outputs.
- Core telemetry from proc/sys continues.

### CPU topology incomplete
Possible causes:
- CPU topology files not exposed by kernel/container policy
- Partial sysfs visibility
- Remote agent has not yet sent topology payload

## Repository Structure
Current top-level structure:

```text
IRQLENS/
├── agent/
├── backend/
├── collector/
├── docs/
│   └── images/
├── frontend/
├── plans/
├── tests/
├── tools/
├── CHANGELOG.md
├── CONTRIBUTING.md
├── README.md
└── run_irqlens.py
```

## Additional Documentation
- `docs/ARCHITECTURE.md`
- `docs/AGENT_PROTOCOL.md`
- `docs/VISUALIZATIONS.md`
- `docs/OPERATIONS.md`
- `docs/images/README.md`

## Contributing
See `CONTRIBUTING.md`.

## License
No project license has been granted. The repository remains unlicensed pending owner approval; do not assume permission to redistribute or sublicense it. A LICENSE file will be added only after the owner selects and approves its terms. Third-party dependencies retain their respective licenses.
