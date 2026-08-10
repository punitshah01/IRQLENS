# IRQLENS

IRQLENS is a Linux IRQ and network diagnostics dashboard for System Under Test (SUT) environments.

It provides:
- live IRQ/SoftIRQ/network telemetry
- historical telemetry persistence in SQLite
- on-demand diagnostic session collection
- export outputs in JSON/CSV/XML/TXT
- session archive download

## Architecture

IRQLENS uses a modular backend:

- `backend/app/main.py`: FastAPI app, REST endpoints, websocket endpoint
- `backend/app/config.py`: centralized environment-driven settings
- `backend/app/store.py`: SQLite telemetry/session/file metadata storage
- `backend/app/ws.py`: websocket broadcast manager
- `backend/app/collectors/`: data collectors
- `backend/app/services/sampler.py`: background live telemetry sampler
- `backend/app/services/diagnostics.py`: session start/stop/export orchestration
- `backend/app/services/exporter.py`: JSON/CSV/XML/TXT writers
- `backend/app/services/health.py`: health + dependency reporting
- `frontend/index.html`: dashboard UI with sidebar pages and live updates

## Data Sources

Primary live telemetry sources:
- `/proc/interrupts`
- `/proc/softirqs`
- `/proc/net/dev`
- `/proc/uptime`
- `/proc/loadavg`
- `/proc/cpuinfo`
- `/proc/meminfo`
- `/sys/class/net/*`
- `/proc/irq/*/smp_affinity_list`
- `/sys/kernel/irq/*/node`

Supplemental diagnostics commands (discovered dynamically):
- `ip`
- `ss`
- `sysctl`
- `ethtool` (optional)

## Compatibility

Designed for Ubuntu and CentOS/RHEL-family Linux systems.

Behavior for missing capabilities:
- missing commands are marked as unavailable and skipped
- unsupported command options are captured in stderr/exit code
- unavailable proc/sys paths are treated as `N/A`
- collector continues even if a subset of sources fails

## Installation

### Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

### Windows backend setup

```powershell
cd backend
python -m venv .venv
. .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

## Startup

Quick start from repository root:

```bash
python3 run_irqlens.py
```

Linux local collector mode (legacy collector compatibility):

```bash
python3 run_irqlens.py --collect-local --nic <interface>
```

The dashboard is served at:

- `http://<host-ip>:8080`

## Running as Root

IRQLENS works in root and non-root mode.

Root-sensitive access (for full diagnostics visibility):
- some `ethtool` paths
- some `/proc/irq` and device metadata paths

Health endpoint reports:
- `running_as_root: true/false`

## Configuration

Set in `backend/.env`:

- `IRQLENS_HOST`
- `IRQLENS_PORT`
- `IRQLENS_DB_PATH`
- `IRQLENS_OUTPUT_DIR`
- `IRQLENS_COLLECTION_INTERVAL`
- `IRQLENS_SUPPORTED_INTERVALS`
- `IRQLENS_METRIC_RETENTION`
- `IRQLENS_RETENTION_DAYS`
- `IRQLENS_MAX_SESSIONS`
- `IRQLENS_MAX_STORAGE_MB`
- `IRQLENS_COMMAND_TIMEOUT_SECONDS`
- `IRQLENS_LOG_LEVEL`
- `IRQLENS_CORS_ORIGINS`
- `IRQLENS_ALLOWED_INGEST_IPS`
- `IRQLENS_DISABLE_INGEST_ALLOWLIST`

Default output directory:
- `/root/irqlens`

## API Endpoints

Core endpoints:

- `GET /api/health`
- `GET /api/system`
- `GET /api/interfaces`
- `GET /api/irq/current`
- `GET /api/irq/history`
- `GET /api/softirq/current`
- `GET /api/network/current`
- `GET /api/network/{interface}`
- `GET /api/sessions`
- `GET /api/sessions/{session_id}`
- `POST /api/sessions/start`
- `POST /api/sessions/{session_id}/stop`
- `GET /api/sessions/{session_id}/files`
- `GET /api/sessions/{session_id}/download`
- `GET /api/files?path=<session-file-path>`
- `WS /ws`

Compatibility endpoints retained:
- `POST /api/irq/ingest`
- `GET /api/irq/latest`
- `GET /api/host/latest`
- `GET /api/summary/current`

## Session Output Structure

IRQLENS writes under:

- `/root/irqlens/sessions/<session-id>/`

Categories are written as:
- `<category>.json`
- `<category>.csv`
- `<category>.xml`
- `<category>.txt`

Command raw output is saved under:
- `/root/irqlens/sessions/<session-id>/commands/*.txt`

Latest session marker:
- `/root/irqlens/latest/session.txt`

Downloadable zip archive:
- `/api/sessions/{session_id}/download`

## Frontend Pages

- Overview
- IRQ Monitor
- SoftIRQ
- CPU
- Network
- Interfaces
- Diagnostics
- Sessions
- Logs
- Settings

Features include:
- websocket status and reconnect indicator
- stale data detection
- sortable/searchable IRQ table
- dynamic interface selector (`ALL INTERFACES` + detected interfaces)
- session start/stop and file links

## Security Notes

IRQLENS does not expose arbitrary shell command execution.

Safe command collection behavior:
- command allowlist only
- executable discovery before run
- timeout-protected subprocess calls
- stdout/stderr/exit_code captured

Session file downloads are constrained to configured output directory.

## Testing

Run:

```bash
cd backend
pytest ..\tests -q
```

Current tests cover:
- IRQ parsing and rate calculation
- SoftIRQ parsing and rate calculation
- network interface discovery parsing
- JSON/CSV/XML/TXT export validity
- health endpoint
- diagnostics session lifecycle endpoint flow

## Troubleshooting

1. No hosts listed
- wait for live sampler startup
- check `/api/health` collector status
- verify backend can read `/proc`

2. Missing command diagnostics
- check `/api/health` dependency list
- install optional tools (`ethtool`, `ss`, `iproute2`, `sysctl`)

3. Stale data indicator
- verify backend process is running
- check websocket connection status
- check collector status in `/api/health`

4. Permission gaps
- run as root for complete IRQ/device metadata coverage

## Development Notes

- Keep live telemetry lightweight and proc/sys based.
- Use command collectors for snapshot diagnostics, not high-frequency loops.
- Add new diagnostics commands only through explicit allowlist in `collectors/commands.py`.
