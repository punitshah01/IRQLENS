# IRQLENS

Platform IRQ, SoftIRQ, and network operations dashboard for Linux SUTs.

IRQLENS turns CLI-only monitoring workflows into a browser dashboard by collecting
high-value counters from `/proc` and presenting both fleet-level and host-level views.

## What IRQLENS solves

- Real-time top IRQ visibility without running `irqtop` interactively
- Per-CPU IRQ load distribution and affinity visibility
- SoftIRQ pressure tracking for packet path health
- Network throughput/packet/drop telemetry from the host
- Multi-SUT summary for side-by-side operational triage

## Key features

- **IRQ Top Table**: IRQ line, source, rate/sec, and affinity list
- **IRQ Detail Table**: IRQ, NIC, queue, RX/TX direction, rate, busiest CPU, active CPU count, and affinity match
- **Queue Activity Summary**: grouped queue-level RX/TX/TxRx rates per NIC
- **CPU IRQ Heatmap**: per-CPU IRQ rate intensity
- **SoftIRQ Table**: highest-rate softirq classes
- **Host KPI strip**: RX/TX throughput, packet rates, drop rates, softirq total
- **Fleet Summary**: all active SUTs in one table
- **Trends Panel**: RX/TX/SoftIRQ time trends for selected host
- **Dual update path**: WebSocket live push + REST polling fallback
- **SQLite retention**: persistent recent history with per-host pruning
- **Optional ingest allowlist**: restrict who can publish samples

## Architecture

```
Linux SUT(s)
  |- collector/irq_collector.py
  |    |- /proc/interrupts
  |    |- /proc/irq/*/smp_affinity_list
  |    |- /proc/softirqs
  |    |- /proc/net/dev
  |
  +---- POST /api/irq/ingest (JSON)
            |
            v
       FastAPI backend (backend/app)
         |- SQLite store
         |- /api/* query endpoints
         +- /ws live notification
            |
            v
       Browser dashboard (frontend/index.html)
         |- Polling fallback
         +- Live refresh on websocket events
```

## Project layout

```
IRQLENS/
+-- backend/
|   +-- app/
|   |   +-- main.py           FastAPI routes + websocket
|   |   +-- store.py          SQLite persistence + retention
|   |   +-- models.py         Pydantic payload models
|   |   +-- config.py         Environment-driven settings
|   |   +-- ws.py             WebSocket connection manager
|   +-- requirements.txt
|   +-- .env.example
|   +-- run_backend.sh
|   +-- run_backend.ps1
+-- collector/
|   +-- irq_collector.py      Linux-side telemetry sampler/publisher
+-- frontend/
|   +-- index.html            Operations dashboard
+-- README.md
```

## Requirements

### Backend host
- Python 3.9+
- Network reachable from SUTs on backend port

### SUT host(s)
- Linux with `/proc/interrupts`, `/proc/softirqs`, `/proc/net/dev`
- Python 3.8+

## Quick start

### One-command start (PRISM-style)

From IRQLENS repo root:

```bash
python3 run_irqlens.py
```

Windows PowerShell:

```powershell
python run_irqlens.py
```

This starts backend + dashboard UI on:

- `http://<detected-host-ip>:8080`

IRQLENS prints the reachable URL in the terminal, similar to PRISM.

If IRQLENS is running on the same Linux SUT you want to monitor, start backend + local collector together:

```bash
python3 run_irqlens.py --collect-local --nic <nic-name>
```

Example:

```bash
python3 run_irqlens.py --collect-local --nic ens3np0
```

When `--collect-local` is used, IRQLENS automatically allows local collector IPs for ingest.
For same-host local mode, IRQLENS also disables ingest allowlist enforcement to avoid multihomed source-IP mismatches.
The collector also bypasses `http_proxy`/`https_proxy` when posting to the backend so local or lab-network ingest is not intercepted by a proxy.

### 1. Start backend

Linux/macOS:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
set -a; . ./.env; set +a
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Windows PowerShell:

```powershell
cd backend
python -m venv .venv
. .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

You can also use:

- `backend/run_backend.sh`
- `backend/run_backend.ps1`

### 2. Start collector on each Linux SUT

```bash
python3 collector/irq_collector.py \
  --server http://<dashboard-host>:8080 \
  --sut-ip <sut-ip-or-name> \
  --nic <nic-name> \
  --interval 1.0 \
  --topn 64
```

Notes:
- `--nic` optional. If omitted, aggregate all interfaces in `/proc/net/dev`.
- First sample cycle is warm-up for delta-based metrics.

### 3. Open the dashboard

Open `http://<dashboard-host>:8080` in browser. The backend serves the dashboard page at `/`.

## Configuration

Backend environment variables (`backend/.env.example`):

| Variable | Default | Meaning |
|---|---|---|
| `IRQLENS_DB_PATH` | `./data/irqlens.db` | SQLite database location |
| `IRQLENS_METRIC_RETENTION` | `5000` | Max rows retained per host per table |
| `IRQLENS_CORS_ORIGINS` | `*` | Comma-separated CORS origins |
| `IRQLENS_ALLOWED_INGEST_IPS` | empty | Optional client IP allowlist for ingest |

Collector arguments:

| Argument | Default | Meaning |
|---|---|---|
| `--server` | required | Backend URL |
| `--sut-ip` | required | Host identifier shown in dashboard |
| `--interval` | `1.0` | Sampling interval seconds |
| `--topn` | `64` | Top IRQ lines sent per sample |
| `--nic` | empty | Interface to monitor for net stats |

## API reference

### Health
- `GET /health`

### Ingest
- `POST /api/irq/ingest`
  - body:
    - `samples[]`: IRQ samples
    - `host_samples[]`: host/network/softirq samples

### Query
- `GET /api/hosts`
- `GET /api/irq/latest?sut_ip=<host>&limit=300`
- `GET /api/host/latest?sut_ip=<host>&limit=120`
- `GET /api/summary/current`

### Live channel
- `WS /ws`
  - Backend sends ingest notifications to trigger client refresh.

## Operational guidance

### Security
- Set `IRQLENS_ALLOWED_INGEST_IPS` in production to permit only trusted SUTs.
- Prefer private network exposure for backend ingest path.

### Retention sizing
- Increase `IRQLENS_METRIC_RETENTION` for longer trend windows.
- Keep default for low disk usage and fast latest-query response.

### Performance tuning
- Start with `--interval 1.0` and `--topn 64`.
- Raise `--interval` if backend or network overhead must be reduced.

## Troubleshooting

### No hosts visible
- Verify collector can reach backend URL.
- Check backend logs for `403 ingest client IP not allowed` if allowlist is set.
- If you only started `run_irqlens.py`, the UI will load but remain empty until a collector posts data.
- For same-host monitoring on Linux, use `python3 run_irqlens.py --collect-local --nic <nic-name>`.
- If no data is present yet, the host dropdown will show `Waiting for collector data...`.

### Host appears but no rates
- First loop establishes baseline; wait one interval.
- Confirm collector has permission to read `/proc/interrupts` and `/proc/softirqs`.

### SoftIRQ or net values look zero
- Confirm traffic exists on selected NIC.
- Remove `--nic` to aggregate all interfaces for validation.

### Dashboard not live-updating instantly
- WebSocket may be blocked by network policy.
- REST polling fallback runs automatically every 5 seconds.

## Current scope and roadmap

Implemented now:
- IRQ + SoftIRQ + network telemetry path
- SQLite persistence with retention
- Multi-SUT fleet summary + selected-host deep view
- WebSocket + polling update model

Planned next:
- Additional collectors (`ethtool -S`, `ss -s`, `nstat`)
- Optional authentication token for ingest
- Export/report snapshots
