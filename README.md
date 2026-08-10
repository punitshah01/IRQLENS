# IRQLENS

Linux IRQ, SoftIRQ, CPU, network, and system performance monitoring and diagnostics dashboard.

[![Python](https://img.shields.io/badge/Python-Repository%20does%20not%20pin%20minor%20version-2f7ed8)](#requirements) [![Tests](https://img.shields.io/badge/Tests-pytest%20(local%20command)-2f7ed8)](#testing) [![License](https://img.shields.io/badge/License-Not%20specified-b76e00)](#license) [![Status](https://img.shields.io/badge/Status-Active%20development-2f7ed8)](#overview)

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
- Code uses modern typing syntax (`|`) and is expected to run on Python 3.10+.
- This workspace was executed with Python 3.13 (`.venv`).

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
- SQLite database growth is bounded by row-retention trimming per SUT (`IRQLENS_METRIC_RETENTION`).
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
6. Verify SUT appears as `ONLINE` in Systems view.
7. Open dashboard in browser: `http://<server>:8080`.
8. Select SUT from host selector.
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
- Server-side setting `IRQLENS_AGENT_TOKEN` controls bearer-token validation.
- Empty server token means agent auth is not enforced.

Permissions and host requirement:
- Agent is intended for Linux only.
- Agent reads `/proc` and `/sys` directly on SUT.

How to verify connection:
- UI Systems page shows SUT status (`ONLINE`, `STALE`, `OFFLINE`).
- API: `GET /api/systems`.
- UI WebSocket status should show `Connected`.

## Dashboard Pages
Implemented pages in `frontend/index.html`:

### Overview
- Purpose: High-level health and trends for selected SUT or fleet summary.
- Shows: KPI cards, IRQ+network trend, CPU IRQ heatmap, top IRQ bar, interface heatmap, SoftIRQ distribution, correlation sankey, health meters, activity timeline.
- Data sources: `/api/health`, `/api/summary/current`, `/api/systems/{sut_id}/visualization`, plus live refresh triggers from `/ws` events.

### Systems
- Purpose: List local and remote registered systems.
- Shows: status, address, host info, CPU count, interface count, mode.
- Data source: `/api/systems`.

### IRQ Monitor
- Purpose: Detailed IRQ table and IRQ-focused visualizations.
- Shows: IRQ/sec, counts, affinity, NUMA, CPU distribution, source class.
- Data sources: `/api/irq/current`, `/api/systems/{sut_id}/visualization`.

### SoftIRQ
- Purpose: SoftIRQ rates by class and trend.
- Shows: class rates and time trend.
- Data sources: `/api/softirq/current`, visualization payload softirq series.

### CPU
- Purpose: CPU-centric interrupt distribution and topology.
- Shows: CPU grid heatmap, NUMA IRQ bar chart, CPU topology tree, CPU IRQ table.
- Data sources: visualization payload + `/api/systems/{sut_id}/visualization/topology`.

### Network
- Purpose: Throughput and per-interface behavior.
- Shows: totals, trend, ranking, error/drop trend, interface table.
- Data sources: `/api/network/current`, visualization payload network series.

### Interfaces
- Purpose: Interface metadata view.
- Shows: state, MTU, MAC, speed, duplex, driver, IP lists.
- Data source: `/api/interfaces`.

### Diagnostics
- Purpose: one-click snapshot collection.
- Shows: category selectors, start/stop actions, generated file links.
- Data sources: `/api/sessions/start`, `/api/sessions/{id}/stop`, `/api/files`.

### Sessions
- Purpose: historical session catalog and downloads.
- Shows: session metadata, files, zip download.
- Data sources: `/api/sessions`, `/api/sessions/{id}/files`, `/api/sessions/{id}/download`.

### Logs
- Purpose: UI event stream for connection/refresh/diagnostic actions.
- Data source: browser-side event log from API/WebSocket actions.

### Compare
- Purpose: side-by-side two-SUT metric comparison.
- Data source: `/api/visualization/compare`.

### Settings
- Purpose: display runtime interval/dependency status and tune frontend anomaly thresholds.
- Data source: `/api/health`.

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

### Network heatmap
- Rows: interfaces
- Metric selector: `rx_bps`, `tx_bps`, `rx_pps`, `tx_pps`, `errors_ps`, `drops_ps`
- Cell intensity: selected metric value per interface

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
- UI threshold sliders filter displayed timeline events.

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
- `ip`: addresses, links, routes, neighbors
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

Retention behavior:
- Metric tables are trimmed per SUT to `IRQLENS_METRIC_RETENTION` rows.
- Day/session/storage-limit settings are present in config but currently not actively enforced by cleanup jobs.

## Diagnostic Collection
Implemented flow:

```text
Start Session
      |
Collect selected categories from store/local collectors
      |
Run allowlisted diagnostic commands (local mode only)
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

## Output Directory Structure
Default root: `/root/irqlens`

```text
/root/irqlens/
├── latest/
│   └── session.txt
└── sessions/
    ├── <session-id>/
    │   ├── metadata.json
    │   ├── irq/
    │   │   ├── irqtop.json
    │   │   ├── irqtop.csv
    │   │   ├── irqtop.xml
    │   │   └── irqtop.txt
    │   ├── softirq/
    │   │   ├── softirq.json
    │   │   ├── softirq.csv
    │   │   ├── softirq.xml
    │   │   └── softirq.txt
    │   ├── network/
    │   │   ├── network.json
    │   │   ├── network.csv
    │   │   ├── network.xml
    │   │   └── network.txt
    │   ├── system/
    │   │   ├── system.json
    │   │   ├── system.csv
    │   │   ├── system.xml
    │   │   └── system.txt
    │   └── commands/
    │       ├── commands.json
    │       ├── commands.csv
    │       ├── commands.xml
    │       ├── commands.txt
    │       └── <command-name>.txt
    └── <session-id>.zip
```

Local legacy collector (`collector/irq_collector.py`) also writes artifacts to `IRQLENS_ARTIFACT_DIR` (default `./artifacts`).

## Security
Current implemented controls:
- Optional agent bearer-token auth (`IRQLENS_AGENT_TOKEN`)
- Optional ingest IP allowlist (`IRQLENS_ALLOWED_INGEST_IPS`) for legacy ingest API
- Command execution is restricted to allowlisted diagnostics in `DiagnosticCommandCollector`
- No arbitrary shell execution API exists
- File download endpoint validates requested paths are under configured output root

Important operational warning:
- Do not expose IRQLENS management/API endpoints to untrusted networks without additional controls (TLS termination, network ACLs, authentication gateway, and access restrictions).

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
- Legacy ingest compatibility: `/api/irq/ingest`, `/api/irq/latest`, `/api/host/latest`, `/api/summary/current`

WebSocket:
- Browser uses `/ws` for event-driven refresh triggers.
- Agent does not use WebSocket in current implementation; it uses HTTP POST telemetry + heartbeat.

## Configuration Reference
From `backend/.env.example` and `backend/app/config.py`:

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
| `IRQLENS_AGENT_HEARTBEAT_INTERVAL` | Suggested heartbeat interval returned to agent | `5` | No |
| `IRQLENS_AGENT_STALE_THRESHOLD` | Stale/offline threshold basis | `15` | No |

## Testing
Repository test command:

```bash
python -m pytest -q
```

Current implemented coverage areas:
- IRQ parser/rate behavior
- SoftIRQ parser/rate behavior
- Network interface discovery and parsing
- Export engine JSON/CSV/XML/TXT output
- API health and session endpoints
- Remote agent register/heartbeat/telemetry routing
- Visualization math (`irq_balance_score`, `detect_spikes`)
- Visualization API payload and topology endpoint

Current status from local run in this workspace:
- `10 passed`, plus FastAPI deprecation warnings (`on_event`).

Not currently present as dedicated automated suites:
- Standalone frontend unit tests
- Dedicated WebSocket test module
- End-to-end browser automation suite

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
LICENSE file not present; project license should be selected by the repository owner.
