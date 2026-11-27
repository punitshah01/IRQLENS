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
- [CLI: Trigger Captures from Benchmark Scripts](#cli-trigger-captures-from-benchmark-scripts)
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
- Monitor block-device I/O (IOPS, throughput, await, queue depth) with NVMe queue and IRQ metadata
- Enumerate PCIe cards and flag link downgrades and AER errors
- Flag cross-NUMA IRQ handling, softnet backlog drops, and report `irqbalance` state
- Collect system/network/storage diagnostic snapshots
- Save raw command outputs
- Export strict JSON, CSV, and TXT artifacts plus one Excel workbook per capture
- Support multiple SUTs
- Push live UI refresh events through WebSocket
- Run air-gapped: no CDN, no external database, no hosted service

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
- Supported: Python 3.9 to 3.14. CI runs the backend tests on 3.9, 3.13, and 3.14 (Linux and Windows).
- The pinned dependencies install from prebuilt wheels only, so no compiler or Rust toolchain is needed. Python 3.15 and newer are not supported yet (no pydantic release ships wheels for both 3.9 and 3.15); the launcher stops with a message instead of trying to compile.
- Repository does not declare a strict minor version in metadata.

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
  - `/proc/net/softnet_stat`
  - `/proc/diskstats`, `/sys/block/*/queue/*`, `/sys/class/nvme/*`
  - `/sys/bus/pci/devices/*`
  - `/sys/class/net/*/device/msi_irqs/`

### Required and optional command-line tools
- Required for core app startup: Python runtime and pip.
- Optional command enrichments (auto-detected): `ip`, `ss`, `ethtool`, `sysctl`, `lscpu`, `numactl`, `lsblk`, `nvme`.
- Core telemetry continues if optional tools are missing.

### Ports
- Default server bind: `0.0.0.0:10000` (all interfaces, no sign-in). `--port` changes the port; `--host 127.0.0.1` serves the server host only.
- `--token <value>` (or `IRQLENS_TOKEN`) requires that token from every browser, script, and agent.
- Browser connects to server over HTTP and WebSocket on the same port.
- Agent sends HTTP POSTs to server on server port (default 10000).

### Permissions
- Non-root works for core telemetry in many environments.
- Root may be required for complete IRQ/device metadata depending on kernel/security policy.

### Disk
- Every time-series table keeps the last `IRQLENS_RETENTION_MINUTES` (default 30) per SUT, however many IRQs or interfaces the SUT has. See [Data Storage](#data-storage).
- Session artifacts are written under `IRQLENS_OUTPUT_DIR` (default `./captures` in the repository root, created on first start). They are not cleaned up automatically.

### Browser
- Modern browser required for ES modules/features and WebSocket.
- ECharts 5.5.1 is vendored at `frontend/static/echarts.min.js`; the dashboard makes no external requests.

## Installation

### Option A: clone and run
The launcher creates `backend/.venv`, installs `backend/requirements.txt` on first start only, and serves the dashboard on `http://<host-ip>:10000` (it prints the URL).

```bash
git clone <repository-url>
cd IRQLENS
python3 run_irqlens.py
```

Air-gapped hosts: the first start needs the packages in `backend/requirements.txt` from a reachable index or wheel directory (`PIP_INDEX_URL` / `PIP_FIND_LINKS`). Later starts need no network.

### Option B: backend-only explicit setup
```bash
git clone <repository-url>
cd IRQLENS/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 10000
```

### Windows backend setup
```powershell
git clone <repository-url>
cd IRQLENS\backend
python -m venv .venv
. .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 10000
```

On Windows the server and dashboard run, but local telemetry is empty because `/proc` and `/sys` do not exist; use it as the server for remote Linux SUTs.

## Quick Start
1. Clone the repository.
2. Start IRQLENS server: `python3 run_irqlens.py` (add `--port <n>` for a port other than 10000).
3. Open the URL it prints: `http://<server-ip>:10000`.
4. Remote SUTs only: on each Linux SUT, start the IRQLENS agent with the server URL.
5. Verify the SUT appears as `ONLINE` in the header SUT dropdown or `GET /api/systems`.
6. Pick the SUT in the header dropdown. The UI restores the last selection, otherwise prefers a remote SUT and falls back to the local system.
7. Confirm live updates (`WebSocket: connected`).
8. Review IRQ, SoftIRQ, CPU, Network, and Storage & PCIe views.
9. Start a capture in Collect Logs (or from a script, see [CLI](#cli-trigger-captures-from-benchmark-scripts)) and download the Excel workbook or ZIP.

## Start IRQLENS Server
```bash
# from repo root
python3 run_irqlens.py              # http://<host-ip>:10000
python3 run_irqlens.py --port 8080  # http://<host-ip>:8080
```

The server listens on all interfaces and asks for no sign-in: anyone who can reach the port can use the dashboard, start captures, and delete sessions. The launcher prints the URL to open.

Optional restrictions:

```bash
python3 run_irqlens.py --host 127.0.0.1   # this host only
python3 run_irqlens.py --token <token>    # browsers, scripts, and agents must present the token
```

With `--host 127.0.0.1`, or when a firewall blocks the port, forward it over SSH from your own machine and open `http://127.0.0.1:10000` there:

```bash
ssh -L 10000:127.0.0.1:10000 <user>@<sut>
```

Launcher options: `--host` (default `0.0.0.0`), `--port` (default 10000), `--token`, `--interval` (local sampling interval in seconds). `--remote` is still accepted and has no effect.

Starting the backend directly (no launcher) binds whatever uvicorn is told and is unauthenticated unless a token variable is set:

```bash
# from backend/
uvicorn app.main:app --host 127.0.0.1 --port 10000
```

Runtime details:
- Bind address: launcher `--host` / `--port` (default `0.0.0.0:10000`)
- DB location: `IRQLENS_DB_PATH` (default from `run_irqlens.py`: `backend/data/irqlens.db`)
- Session/log output root: `IRQLENS_OUTPUT_DIR` (default `./captures`)
- Log level: `IRQLENS_LOG_LEVEL` (default `INFO`)

## Install IRQLENS Agent on SUT
Agent implementation path: `agent/main.py`. Add `--token <token>` when the server was started with one.

```bash
# on Linux SUT
cd IRQLENS
python3 agent/main.py \
  --server http://<irqlens-server>:10000 \
  --sut-id <sut-id> \
  --name <display-name> \
  --telemetry-interval 1.0 \
  --heartbeat-interval 5.0
```

Agent configuration options:
- `--server` (or `IRQLENS_AGENT_SERVER`)
- `--sut-id` (or `IRQLENS_AGENT_SUT_ID`)
- `--name` (or `IRQLENS_AGENT_NAME`)
- `--token` (or `IRQLENS_AGENT_TOKEN`, then `IRQLENS_TOKEN`)
- `--telemetry-interval` (or `IRQLENS_AGENT_TELEMETRY_INTERVAL`)
- `--heartbeat-interval` (or `IRQLENS_AGENT_HEARTBEAT_INTERVAL`)
- `--include-virtual-disks` to also report `loop*`, `ram*`, `dm-*`, `md*`, and `zram*` block devices

The agent needs only the Python standard library plus `pydantic` (it imports the collectors from `backend/app/collectors`). Storage, PCIe, and softnet telemetry require agent 1.2.0 or newer; older agents keep working without those views.

Authentication:
- `IRQLENS_TOKEN` (or `run_irqlens.py --token`) is the single shared token for the dashboard, API, downloads, WebSocket, and agents.
- `IRQLENS_API_TOKEN` (dashboard/API) and `IRQLENS_AGENT_TOKEN` (agents) are optional per-surface overrides for deployments that want separate credentials. Each falls back to the other when only one is set.
- No token configured means authentication is disabled, which is the launcher's default. See [Security](#security).

Permissions and host requirement:
- Agent is intended for Linux only.
- Agent reads `/proc` and `/sys` directly on SUT.

How to verify connection:
- The header SUT dropdown lists every registered SUT with its status (`ONLINE`, `STALE`, `OFFLINE`).
- API: `GET /api/systems`.
- UI WebSocket status should show `connected`.

## Dashboard Pages
The six primary views follow [IRQLENS UI Rules](docs/IRQLENS_UI_RULES.md):

### Overview
Current IRQ/SoftIRQ/network KPIs, IRQ and network trends, CPU/NUMA matrix, and findings for the selected SUT. Findings include IRQ concentration, cross-NUMA IRQ handling, PCIe link downgrades, AER errors, softnet backlog drops, and packet drops.

### CPU
Topology-based CPU matrix with IRQ, SoftIRQ, and utilization metrics, per-CPU detail, and utilization history.

### IRQ
Ranked interrupt sources, IRQ-to-CPU heatmap, search/filter controls, and IRQ metadata table with a Cross-NUMA column. The heatmap shows the Top 25 active IRQs (rate > 0) by default; a dropdown selects 25 / 50 / 100 / All, and a second one collapses the columns per CPU, per NUMA node, or per socket for high-core-count servers.

### Network
Interface selection, throughput trends, packet errors/drops, softnet drops and time-squeeze counts, related IRQs, and metadata including IPv4/IPv6 addresses.

### Storage & PCIe
KPI strip (total IOPS, read/write MB/s, max await, PCIe link warnings, AER errors), per-device IOPS, throughput, and latency/queue-depth trends, a block-device table (scheduler, `nr_requests`, media type, NUMA node, NVMe MSI-X IRQs), and a PCIe card table (BDF, device/driver, NUMA node, current vs maximum link with a red badge on downgrade, MSI-X IRQ count, AER counters).

### Collect Logs
Named timed captures, category selection, recent capture history, remote-command job status, file downloads, Excel workbook, ZIP archives, and HTML reports.

Header controls: the SUT dropdown switches between registered SUTs, `irqbalance: active|inactive|unknown` shows the daemon state on the selected SUT, and **Pause Live Updates** freezes the monitoring views until resumed. Charts redraw at most once per second regardless of the sampling interval.

There are no separate Systems, Sessions, Settings, Admin, Logs, or Compare pages. System listing and comparison APIs remain available.

## Visualizations
### IRQ heatmap
- X-axis: CPU, NUMA node, or socket (selectable)
- Y-axis: IRQ (Top 25 / 50 / 100 / All active IRQs)
- Cell intensity: IRQ/sec for the IRQ-column pair
- Source: per-IRQ `cpu_rates` from `GET /api/irq/current`, grouped in the browser with the SUT topology

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
- IRQs are mapped to interfaces from `/sys/class/net/<iface>/device/msi_irqs/`. Matching the IRQ label against interface names is used only for interfaces sysfs cannot describe.
- Uses IRQ direction (`RX`, `TX`, `TxRx`, `Other`) to aggregate per-interface IRQ rates.
- Correlation only; does not imply causation.

### Cross-NUMA IRQ handling
- Each IRQ's device node (`/sys/kernel/irq/<irq>/node`, else the device's `numa_node`) is compared with the NUMA node of every CPU handling more than 10% of that IRQ's rate.
- A mismatch sets `cross_numa_flag` on the IRQ row, adds an Overview finding, and fills the Cross-NUMA column in the IRQ table.
- IRQs whose device node is unknown (`-1` / `N/A`) are never flagged.

### Storage and PCIe
- Per-device read/write IOPS, MB/s, average await, requests in flight, and utilization from `/proc/diskstats` deltas.
- PCIe link is flagged degraded when current speed or width is below the device maximum; AER totals are flagged when non-zero and again when they increase between samples.
- Source: `GET /api/systems/{sut_id}/io`.

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
| `/proc/net/softnet_stat` | Per-CPU and total `processed`, `dropped`, `time_squeeze`, `cpu_collision` deltas | Optional enrichment |
| `/proc/diskstats` | Per-device IOPS, MB/s, await, in-flight requests, utilization | Optional (Storage & PCIe view) |
| `/sys/block/<dev>/queue/`, `/sys/block/<dev>/device/` | Scheduler, `nr_requests`, rotational flag, NUMA node, model | Optional enrichment |
| `/sys/class/nvme/<ctrl>/device/msi_irqs/` | NVMe MSI-X IRQ numbers | Optional enrichment |
| `/sys/bus/pci/devices/` | PCIe vendor/device/class, driver, NUMA node, link speed/width, MSI IRQs, AER counters | Optional (Storage & PCIe view) |
| `/sys/class/net/<iface>/device/msi_irqs/` | Authoritative NIC-to-IRQ mapping | Optional enrichment |
| `/proc/<pid>/comm` | `irqbalance` daemon detection | Optional enrichment |
| `/proc/stat` | Per-CPU utilization | Optional (CPU utilization) |
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
- `ethtool`: NIC details (`-i`, `-k`, `-c`), counters (`-S`), ring sizes (`-g`), channels (`-l`), RSS indirection (`-x`)
- `lsblk -J`, `nvme list -o json`, `nvme smart-log /dev/nvmeN`: block-device and NVMe details in the `storage` capture category
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
- `cpu_utilization_samples`: per-CPU utilization per tick
- `softnet_samples`, `storage_samples`, `pcie_samples`: softnet deltas, block-device rates, and PCIe card snapshots
- `systems`: registered systems and status
- `agent_heartbeats`: liveness records
- `sessions`: diagnostic session metadata
- `session_files`: exported file catalog
- `diagnostic_jobs`: persisted remote-command requests and lease/status metadata

Write behavior:
- The database runs in WAL mode (`journal_mode=WAL`, `synchronous=NORMAL`, `busy_timeout=5000`), so dashboard reads do not block sampling. Expect `irqlens.db-wal` and `irqlens.db-shm` next to the database while the server runs.
- Everything collected in one sampling tick (local sampler or one agent telemetry POST) is written in a single transaction.
- Only IRQ lines that fired during a tick are stored, so idle lines on large servers cost nothing.

Retention behavior:
- Retention is time-based: each SUT keeps the last `IRQLENS_RETENTION_MINUTES` (default 30) in every time-series table, including CPU utilization, topology, storage, PCIe, and heartbeats. The number of IRQs, CPUs, or interfaces does not shorten the window.
- The window is measured from that SUT's newest sample, so an agent with a skewed clock keeps its history. The newest tick is always kept, so a SUT that stops reporting still shows its last state.
- Samples inside a running capture's window are never pruned.
- Sessions and capture files are not pruned; delete captures from Collect Logs or the output directory.

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
Generate JSON/CSV/TXT exports per category
      |
On stop: write time-series JSON and one <session-name>.xlsx workbook
      |
Record files in SQLite and optional ZIP archive on download
```

Categories:
- `irq`, `softirq`, `network`, `interfaces`, `routes`, `sockets`, `ethtool`, `system`, `storage`, `pcie`

Output formats:
- JSON
- CSV
- TXT
- XLSX (one workbook per capture)
- ZIP (session archive endpoint)

JSON files are strict JSON: `NaN` and `Infinity` are written as `null`, and every file uses the same envelope:

```json
{"schema_version": "1.0", "sut_id": "local", "session_id": "fio_run1", "captured_at": "2026-01-01T12:00:00.000Z", "sampling_interval_s": 1.0, "data": {}}
```

CSV files are UTF-8 with a BOM so Excel opens them directly; nested values are flattened into dotted columns, and text cells that Excel would execute as formulas are prefixed with `'`. The IRQ CSV replaces the per-CPU map with `total_irq_s`, `top_cpu`, `top_cpu_irq_s`, `numa_node`, `cross_numa_flag`, and `active_cpu_count`; the raw per-CPU rates remain in `irqtop.json`.

The workbook `<session-name>.xlsx` has flat sheets `Summary`, `IRQ_Top`, `SoftIRQ`, `Network`, `Softnet`, `Storage`, `PCIe_Cards`, and `TimeSeries_Metrics`, with frozen header rows, sized columns, and number formats. It is written when the capture stops; `GET /api/sessions/{session_id}/workbook` returns it and rebuilds it if the file is missing.

All artifacts are written to `<file>.tmp` and renamed into place, so a reader never sees a half-written file.

Remote commands require agent version 1.1.0 or newer (1.2.0 for the `storage` category). A separate agent worker polls every two seconds and executes only fixed diagnostic categories on the selected SUT. Jobs survive server restarts; an unacknowledged running job can be retried after its five-minute lease expires. No arbitrary command text is accepted from the server.

Capture details show `queued`, `running`, `completed`, or `failed` remote jobs. A timed capture can finish while its commands are still pending; wait for terminal job states before taking a final ZIP/report. Downloads are point-in-time snapshots. Late results invalidate the report status so it can be regenerated. Missing tools, permissions, timeouts, and output limits are preserved in command results, not reported as successful collection.

Command batches have a 120-second budget checked between commands, at most 500 executed commands, and an approximately 4 MiB output budget. Individual streams are capped at 65,536 characters with a truncation warning. Remote per-command timeouts are capped at 30 seconds.

## Output Directory Structure
### Capture Quality
Each completed capture includes `capture_quality.json`, a file inventory with sizes and SHA-256 hashes, and `metadata/capture_config.json` recording the requested duration and sampling interval at capture start. New captures export time-series files only for selected categories. Metadata is refreshed when reports and ZIPs are generated.

In Collect Logs, open a capture and use **Check Evidence** to recalculate its quality. The authenticated endpoint is `POST /api/sessions/{session_id}/quality`; session detail includes the saved result as `capture_quality`. Checks do not regenerate telemetry or move the capture's start/end timestamps. Running captures cannot be manually rechecked.

- `COMPLETE`: all required evidence checks passed. This is not a workload or silicon PASS.
- `INCOMPLETE`: missing/unusable snapshots, unknown sampling configuration, missing files, failed/pending commands, or insufficient stream coverage.
- `CAPTURING`: capture is still running.
- `NOT_EVALUATED`: no saved quality result, typically for an older capture.

Required streams need at least two unique samples, at least 90% of the expected sample count, and no gap larger than 2.5 sampling intervals, including both capture boundaries. Invalid, duplicate, out-of-window, and out-of-order timestamps fail completeness. Per-interface coverage is checked for network captures. CPU frequency is optional. Old captures without recorded sampling configuration remain incomplete; no interval is guessed.

The manifest describes current evidence and provides hashes for downstream integrity checks; rechecking recomputes those hashes, not a comparison against a trusted original. Clock synchronization, counter correctness, IRQ mapping, performance thresholds, and silicon qualification are not evaluated.

### Files
Default root: `./captures` in the repository root (`IRQLENS_OUTPUT_DIR`)

```text
captures/
+-- latest/session.txt
+-- <session-name>/
|   +-- <session-name>.xlsx                 (written when the capture stops)
|   +-- metadata/metadata.json
|   +-- metadata/capture_config.json
|   +-- summary.json
|   +-- capture_quality.json
|   +-- irq/irqtop.{json,csv,txt}
|   +-- softirq/softirq.{json,csv,txt}
|   +-- network/network.{json,csv,txt}
|   +-- network/softnet.{json,csv,txt}
|   +-- interfaces/interfaces.{json,csv,txt}
|   +-- system/system.{json,csv,txt}
|   +-- storage/storage.{json,csv,txt}
|   +-- pcie/pcie.{json,csv,txt}
|   +-- commands/commands.{json,csv,txt}    (local)
|   +-- commands/command_001.txt            (local raw output)
|   +-- commands/<job-id>/                  (remote)
|   |   +-- commands.{json,csv,txt}
|   |   +-- command_001.txt
|   |   +-- status.json
|   +-- timeseries/                         (JSON history per selected category)
|   +-- report.html                         (generated on request)
+-- <session-name>.zip                      (generated on download)
```

Captures made by earlier versions keep their XML files and unwrapped JSON and still list in Collect Logs.

Upgrading: the default output directory was `/root/irqlens`. Captures recorded under a directory other than the current `IRQLENS_OUTPUT_DIR` still open, but their files cannot be downloaded, and deleting one removes only its record, never files outside the output directory. Start the server with `IRQLENS_OUTPUT_DIR=/root/irqlens` to keep serving them.

## Security
Current implemented controls:
- `python run_irqlens.py` listens on all interfaces without authentication by default: anyone who can reach the port has full access to the dashboard and API. `--host 127.0.0.1` limits it to the server host; `--token <value>` or `IRQLENS_TOKEN` requires a token.
- When configured, one shared token (`IRQLENS_TOKEN`) protects pages, telemetry reads, management mutations, session exports, API docs, WebSocket access, and every `/api/agent/*` endpoint.
- `IRQLENS_API_TOKEN` and `IRQLENS_AGENT_TOKEN` optionally override the shared token per surface. Each falls back to the other when only one is configured. Configure distinct values to prevent agent credentials from accessing dashboard/management APIs.
- Browser sign-in uses HTTP Basic: username `irqlens`, password equal to the dashboard/API token. Scripts use `Authorization: Bearer <token>`. Credentials are not stored in browser local storage or URL query strings.
- Browser sign-in issues a signed 12-hour HttpOnly, SameSite=Strict session cookie for subsequent requests and WebSockets; it is Secure over HTTPS and contains no API token. Token rotation invalidates existing cookies. Native HTTP Basic credential caching remains browser-controlled.
- Cross-origin mutations and WebSocket connections require the same origin or an explicit `IRQLENS_CORS_ORIGINS` entry; `*` does not grant this permission.
- `/health` is an intentionally unauthenticated, minimal liveness endpoint.
- Command execution is restricted to allowlisted diagnostics in `DiagnosticCommandCollector`
- No arbitrary shell execution API exists
- File download endpoint validates requested paths are under configured output root and answers with `X-Content-Type-Options: nosniff`

Important operational warning:
- Authentication is disabled when no token is configured, which is the default. Run it that way only on networks where every host that can reach the port is trusted; otherwise set a token. Restart the server after changing token configuration.
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
Browser  --->  IRQLENS Server :10000 (HTTP + WS)
Agent    --->  IRQLENS Server :10000 (HTTP POST)
```

The launcher listens on all interfaces by default; with `--host 127.0.0.1` neither flow works from another host.

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
- Storage, PCIe, softnet: `/api/systems/{sut_id}/io`
- Sessions/files: `/api/sessions*`, `/api/files`
- Agent protocol: `/api/agent/register`, `/api/agent/heartbeat`, `/api/agent/telemetry`
- Remote commands: `/api/agent/diagnostics/poll`, `/api/agent/diagnostics/{job_id}/results`
- Read-only compatibility: `/api/irq/latest`, `/api/host/latest`, `/api/summary/current`

Download endpoints (`/api/sessions/{session_id}/workbook`, `/api/sessions/{session_id}/download`, `/api/files?path=...`) return the file bytes unchanged with `Content-Disposition: attachment; filename="..."` and a matching `Content-Type` (`application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`, `application/zip`, `application/json`, `text/csv`). The dashboard fetches them as blobs and shows an error toast for any non-200 response.

WebSocket:
- Browser uses `/ws` for event-driven refresh triggers.
- Agent does not use WebSocket in current implementation; it uses HTTP POST telemetry + heartbeat.

## CLI: Trigger Captures from Benchmark Scripts
`POST /api/sessions` starts a timed capture (`POST /api/sessions/start` is the same endpoint). `duration_seconds` must be one of 30, 60, 120, 300, 600, 900, 1800; the capture stops itself after that time or earlier through `/stop`. `session_name` must be unique and match `[A-Za-z0-9][A-Za-z0-9_.-]*`. Omit `sut_id` (or use `local`) for the server host; use an agent's `--sut-id` for a remote SUT.

```bash
BASE=http://127.0.0.1:10000
NAME=fio_randread_run1

# start a capture of up to 5 minutes
curl -fsS -X POST "$BASE/api/sessions" -H 'Content-Type: application/json' \
  -d "{\"session_name\": \"$NAME\", \"duration_seconds\": 300, \"sut_id\": \"local\", \"categories\": [\"irq\", \"softirq\", \"network\", \"system\", \"storage\", \"pcie\"]}"

./run_benchmark.sh

# stop when the benchmark ends
curl -fsS -X POST "$BASE/api/sessions/$NAME/stop" -H 'Content-Type: application/json' -d '{"reason": "manual"}'

# download the Excel workbook and the full ZIP (file names come from the server)
curl -fsS -OJ "$BASE/api/sessions/$NAME/workbook"
curl -fsS -OJ "$BASE/api/sessions/$NAME/download"
```

If the server was started with a token, add `-H "Authorization: Bearer $IRQLENS_TOKEN"` to every call. Behind an HTTP proxy, export `no_proxy=127.0.0.1,localhost` first.

The same flow in Python, standard library only:

```python
import json
import subprocess
import time
import urllib.request

BASE = "http://127.0.0.1:10000"
HEADERS = {"Content-Type": "application/json"}  # with a server token: add "Authorization": "Bearer <token>"


def call(path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(BASE + path, data=data, headers=HEADERS, method="POST" if data else "GET")
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


name = f"fio_randread_{int(time.time())}"
call("/api/sessions", {"session_name": name, "duration_seconds": 300, "sut_id": "local"})
try:
    subprocess.run(["./run_benchmark.sh"], check=True)
finally:
    call(f"/api/sessions/{name}/stop", {"reason": "manual"})

with open(f"{name}.xlsx", "wb") as workbook:
    workbook.write(call(f"/api/sessions/{name}/workbook"))
```

## Configuration Reference
From `backend/app/config.py`. Set environment variables in the launching shell; a `.env` file is not automatically loaded. The bind address comes from the launcher's `--host`/`--port` flags (or uvicorn's when the backend is started directly); `IRQLENS_HOST` and `IRQLENS_PORT` only record it.

| Setting | Description | Default | Required |
|---|---|---|---|
| `IRQLENS_HOST` | Bind address recorded by the launcher | `127.0.0.1` | No |
| `IRQLENS_PORT` | Bind port recorded by the launcher | `10000` | No |
| `IRQLENS_DB_PATH` | SQLite DB file path | `./data/irqlens.db` | No |
| `IRQLENS_OUTPUT_DIR` | Session/output root | `./captures` (repository root) | No |
| `IRQLENS_COLLECTION_INTERVAL` | Local sampler interval (seconds) | `1.0` | No |
| `IRQLENS_SUPPORTED_INTERVALS` | UI interval options | `0.1,0.25,0.5,1,2,5,10` | No |
| `IRQLENS_RETENTION_MINUTES` | Minutes of history kept per SUT in every time-series table | `30` | No |
| `IRQLENS_STORAGE_INCLUDE_VIRTUAL` | Also sample `loop*`, `ram*`, `dm-*`, `md*`, `zram*` block devices locally (`1` to enable) | `0` | No |
| `IRQLENS_COMMAND_TIMEOUT_SECONDS` | Diagnostic command timeout | `8.0` | No |
| `IRQLENS_LOG_LEVEL` | Backend log level | `INFO` | No |
| `IRQLENS_CORS_ORIGINS` | CORS allow list | `*` | No |
| `IRQLENS_TOKEN` | Single shared token for dashboard, API, WebSocket, and agents | empty | No (unset = no sign-in) |
| `IRQLENS_AGENT_TOKEN` | Agent-only override of the shared token | empty | No |
| `IRQLENS_API_TOKEN` | Dashboard/API-only override of the shared token | empty | No |
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

On Linux CI, use `python -m playwright install --with-deps chromium`. Browser tests explicitly skip when their optional dependencies are absent. CI runs backend tests on Linux/Windows with Python 3.9, 3.13, and 3.14 and browser tests on Linux.

Without internet access for the browser download, run the browser tests against an installed Edge or Chrome:

```bash
python -m pytest tests -q --browser chromium --browser-channel msedge   # or: --browser-channel chrome
```

Coverage includes API and WebSocket authentication, origin rejection, remote command routing/retry/export, interface address discovery, timeout decoding, strict enveloped JSON, CSV and workbook exports, byte-exact typed downloads, WAL mode and time-based retention across all tables, the softnet, diskstats, PCIe, and IRQ-mapping parsers and cross-NUMA logic against mocked `/proc` and `/sys` trees (Linux and Windows), frontend navigation, the Storage & PCIe view, SUT switching, live-update throttling, blob downloads, and untrusted telemetry escaping. Real Linux driver permissions and live multi-SUT/network-failure scenarios still require SUT validation.

## Validation
Validation tool:
- `tools/validate_sut_visualization.py`

Example (Linux full local source + API checks):

```bash
python3 tools/validate_sut_visualization.py \
  --server-url http://<server>:10000 \
  --sut-id <sut-id> \
  --mode local \
  --sample-interval 1.0
```

Example (non-Linux host validating API payload path only):

```bash
python tools/validate_sut_visualization.py \
  --server-url http://127.0.0.1:10000 \
  --sut-id <sut-id> \
  --mode api-only
```

PASS/WARN/FAIL semantics:
- `PASS`: check succeeded
- `WARN`: non-blocking absence/idle/limited environment condition
- `FAIL`: blocking failure; script exits non-zero

## Troubleshooting
### Browser cannot reach the dashboard
- Timeout on `http://<sut>:10000`: a firewall between you and the SUT blocks that port. Start the server on a port that is open (`--port <n>`), or forward the port over SSH (`ssh -L 10000:127.0.0.1:10000 <user>@<sut>`) and open `http://127.0.0.1:10000`.
- "127.0.0.1 refused to connect": `127.0.0.1` in your browser is your own machine, not the SUT. Use the URL the launcher printed, or the SSH forward above.

### Agent does not connect
Check:
- Server was not started with `--host 127.0.0.1` (this host only)
- Agent server URL (`--server`) and port reachability
- Server is listening on expected interface/port
- Agent `--token` matches the server token (`IRQLENS_TOKEN`, or `IRQLENS_AGENT_TOKEN` when that override is set)
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

### Storage & PCIe view is empty
Check on SUT:
```bash
cat /proc/diskstats
ls /sys/bus/pci/devices
```
- Remote SUTs need agent 1.2.0 or newer.
- `loop*`, `ram*`, and `dm-*` devices are hidden unless `IRQLENS_STORAGE_INCLUDE_VIRTUAL=1` (local) or `--include-virtual-disks` (agent) is set.
- AER counters and link attributes that the kernel or device does not expose show as `N/A`; this is common on virtual machines.

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
├── docs/
│   └── images/
├── frontend/
│   └── static/        (vendored ECharts)
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
