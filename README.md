# IRQLENS

IRQLENS is a Linux IRQ and network diagnostics dashboard for System Under Test (SUT) environments.

IRQLENS supports both:
- local mode (collector and backend on the same host)
- remote mode (one central backend + one or more Linux SUT agents)

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
- `agent/main.py`: Linux SUT agent for register/heartbeat/telemetry
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

### Remote SUT agent startup (Linux SUT)

Run on each Linux SUT:

```bash
python3 agent/main.py \
	--server http://<irqlens-server>:8080 \
	--sut-id <unique-sut-id> \
	--name <display-name> \
	--token <agent-token>
```

If `IRQLENS_AGENT_TOKEN` is empty on server, token is optional.

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
- `IRQLENS_AGENT_TOKEN`
- `IRQLENS_AGENT_HEARTBEAT_INTERVAL`
- `IRQLENS_AGENT_STALE_THRESHOLD`

Default output directory:
- `/root/irqlens`

## API Endpoints

Core endpoints:

- `GET /api/health`
- `GET /api/system`
- `GET /api/hosts`
- `GET /api/systems`
- `POST /api/systems`
- `GET /api/systems/{sut_id}`
- `DELETE /api/systems/{sut_id}`
- `POST /api/systems/{sut_id}/test`
- `POST /api/agent/register`
- `POST /api/agent/heartbeat`
- `POST /api/agent/telemetry`
- `GET /api/interfaces`
- `GET /api/irq/current`
- `GET /api/irq/history`
- `GET /api/softirq/current`
- `GET /api/network/current`
- `GET /api/network/{interface}`
- `GET /api/systems/{sut_id}/visualization`
- `GET /api/systems/{sut_id}/visualization/topology`
- `GET /api/visualization/compare?a=<sut-a>&b=<sut-b>&window_seconds=<sec>`
- `GET /api/sessions`
- `GET /api/sessions/{session_id}`
- `POST /api/sessions/start`
- `POST /api/sessions/{session_id}/stop`
- `GET /api/sessions/{session_id}/files`
- `GET /api/sessions/{session_id}/download`
- `GET /api/files?path=<session-file-path>`
- `WS /ws`

SUT-aware filtering:
- `GET /api/system?sut_id=<sut-id>`
- `GET /api/interfaces?sut_id=<sut-id>`
- `GET /api/irq/current?sut_id=<sut-id>`
- `GET /api/softirq/current?sut_id=<sut-id>`
- `GET /api/network/current?sut_id=<sut-id>`

Session routing:
- `POST /api/sessions/start` accepts body `{"categories": [...], "sut_id": "<sut-id>"}`
- `POST /api/systems/{sut_id}/sessions/start`
- `POST /api/systems/{sut_id}/sessions/{session_id}/stop`

Visualization endpoint notes:
- `window_seconds` is bounded to `30..3600`.
- `top_n` controls top IRQ rows for heatmap/ranking payloads.
- Payload includes trend series, heatmaps, top sources, distribution, health, and anomaly events derived from real telemetry.

## Visualization Metrics

Derived metrics are deterministic and based on recorded telemetry:

- IRQ rate:
	- `IRQ/sec = delta IRQ count / elapsed seconds`
- SoftIRQ total rate:
	- `sum(softirq_class_rate_i)` for each sample timestamp
- Network totals:
	- `RX B/s = sum(interface.rx_bps)`
	- `TX B/s = sum(interface.tx_bps)`
	- `Errors/s = sum(rx_err_ps + tx_err_ps)`
	- `Drops/s = sum(rx_drop_ps + tx_drop_ps)`

IRQ balance score uses normalized entropy of per-CPU IRQ load:

- Let CPU IRQ rates be `x_i` for `i in [1..N]`, total `T = sum(x_i)`
- Probabilities: `p_i = x_i / T`
- Entropy: `H = -sum(p_i * ln(p_i))`
- Normalized entropy: `Hn = H / ln(N)`
- Balance score: `score = 100 * Hn`, clamped to `[0, 100]`

Reported together with coefficient of variation:

- `mean = T / N`
- `std = sqrt(sum((x_i - mean)^2) / N)`
- `CV = std / mean`

Status mapping:
- `Balanced`: score >= 80 and CV < 0.40
- `Moderately Imbalanced`: score >= 60 and CV < 0.70
- `Highly Imbalanced`: otherwise

Spike detection (timeline events):

- Baseline window uses previous 8 points
- Baseline = mean(previous 8 values)
- Spike when `current >= baseline * multiplier`
- Default multiplier is `2.0` (configurable in UI settings)

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
- Systems
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
