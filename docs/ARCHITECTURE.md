# IRQLENS Architecture

## Overview
IRQLENS is composed of four runtime parts:
- Frontend dashboard (`frontend/index.html`)
- FastAPI backend (`backend/app/main.py`)
- SQLite store (`backend/app/store.py`)
- Optional remote Linux SUT agent (`agent/main.py`)

## Component Responsibilities

## Frontend
- Renders all pages and visualizations with ECharts, vendored under `frontend/static/` (no CDN or other external request).
- Polls/refreshes API data, redrawing at most once per second.
- Opens one WebSocket connection to `/ws` for refresh-trigger events.
- Implements client-side filters (IRQ/CPU/interface), custom range controls, zoom persistence, threshold filtering, and compare views.

## Backend API
- Serves frontend (`GET /`).
- Exposes health, telemetry, visualization, diagnostics, session, and download APIs.
- Accepts remote agent registration/heartbeat/telemetry.
- Broadcasts live update events to connected browser clients.

## Local Collectors in Backend
`TelemetrySampler` uses:
- `IRQCollector`
- `SoftIRQCollector`
- `NetworkCollector`
- `SystemCollector`
- `SoftnetCollector` (`/proc/net/softnet_stat`)
- `StorageCollector` (`/proc/diskstats`, `/sys/block`, `/sys/class/nvme`)
- `PCIeCollector` (`/sys/bus/pci/devices`)

This path provides local-host telemetry as system id `local`. Each tick is written to SQLite in one transaction, followed by retention pruning.

## Remote Agent
- Runs on Linux SUT.
- Reads proc/sys sources with the same collector classes and computes rates.
- Sends registration, heartbeat, and telemetry payloads via HTTP.
- Includes optional CPU topology snapshots.

## Data Storage
SQLite (WAL mode) tables include:
- Telemetry: IRQ, SoftIRQ, network, interfaces, system snapshots, CPU utilization, softnet, storage, PCIe
- Topology snapshots
- Registered systems and heartbeats
- Diagnostic sessions and generated files

Every time-series table is pruned per SUT to the last `IRQLENS_RETENTION_MINUTES`.

## Visualization Pipeline
1. Raw telemetry samples are collected/stored.
2. `/api/systems/{sut_id}/visualization` aggregates bounded window series.
3. Backend computes:
   - top IRQ sources
   - source distribution
   - IRQ/CPU heatmap payload
   - CPU load/balance score
   - interface rankings
   - anomaly events
4. Frontend renders charts/tables and applies local filters.

## Diagnostic and Export Pipeline
1. User starts session with selected categories.
2. Service gathers snapshots from store or local collectors.
3. Local mode optionally runs allowlisted commands.
4. Export engine emits strict, enveloped JSON plus CSV (UTF-8 with BOM) and TXT files, each written to a `.tmp` file and renamed into place.
5. On stop, time-series JSON and one multi-sheet `.xlsx` workbook are written.
6. Session file catalog is stored in SQLite.
7. Session archive endpoint creates ZIP on demand.

## Data Flow
```text
Linux proc/sys -> collectors/agent -> backend store -> visualization APIs -> browser charts
                                               |
                                               +-> diagnostics exports -> filesystem artifacts
```

## Network Flow
- Browser -> server: HTTP + WebSocket
- Agent -> server: HTTP POST (register/heartbeat/telemetry)

## Security Model (Current)
- The launcher binds all interfaces without authentication by default; `--token` adds a shared token and `--host 127.0.0.1` limits it to the server host.
- One token (`IRQLENS_TOKEN`) for dashboard, API, WebSocket, and agents, with optional per-surface overrides.
- Diagnostic command execution constrained to allowlist.
- File download path constrained to output directory.

## Known Architecture Limits
- No built-in TLS termination.
- No protocol version negotiation between agent and server.
- Capture files and session records are not cleaned up automatically.
