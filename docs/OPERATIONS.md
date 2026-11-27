# IRQLENS Operations Guide

## 1. Start and Stop Server

### Start from repository root
```bash
# this host only: 127.0.0.1:10000, no authentication
python3 run_irqlens.py

# agents and other hosts: 0.0.0.0:10000, token required (generated and printed if not given)
python3 run_irqlens.py --remote --token <token>
```

### Start backend explicitly
```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 10000
```

Started this way the server is unauthenticated unless `IRQLENS_TOKEN` is set; do not bind a non-loopback address without it.

### Stop
- Press `Ctrl+C` in the server terminal.

## 2. Start and Stop Agent

### Start on Linux SUT
```bash
python3 agent/main.py \
  --server http://<server>:10000 \
  --sut-id <sut-id> \
  --name <display-name> \
  --token <token>
```

### Stop
- Press `Ctrl+C` in agent terminal or stop supervising service.

## 3. Check Status
- API health: `GET /api/health`
- systems list: `GET /api/systems`
- browser UI status badges: SUT state and WebSocket state

Quick curl checks:
```bash
curl -s http://127.0.0.1:10000/api/health
curl -s http://127.0.0.1:10000/api/systems
```

## 4. Logs
- Server logs: stdout/stderr of uvicorn process
- Agent logs: stdout/stderr of `agent/main.py`
- Browser failures: inspect browser developer tools; there is no dedicated Logs page.

## 5. Database
- SQLite DB path: `IRQLENS_DB_PATH`
- Default from `run_irqlens.py`: `backend/data/irqlens.db`
- Contains telemetry, systems, sessions, and file metadata

Operational notes:
- Back up DB before schema/manual edits.
- The database runs in WAL mode; `irqlens.db-wal` and `irqlens.db-shm` appear next to it while the server runs. Copy all three files, or stop the server first, when backing up.
- Every time-series table keeps the last `IRQLENS_RETENTION_MINUTES` (default 30) per SUT; see section 9.
- Remote diagnostic jobs are persisted in SQLite and reclaimable after a five-minute lease expires.

## 6. Session Files and Downloads
- Output root: `IRQLENS_OUTPUT_DIR` (default `./captures` in the repository root)
- Session files endpoint: `/api/sessions/{session_id}/files`
- Excel workbook endpoint: `/api/sessions/{session_id}/workbook`
- Session archive endpoint: `/api/sessions/{session_id}/download`
- Scripted captures: `POST /api/sessions`, then `POST /api/sessions/{session_id}/stop`; see the CLI section of the README.

## 7. Upgrading
Suggested process:
1. Stop server and agents.
2. Pull repository updates.
3. Reinstall/update dependencies: `pip install -r backend/requirements.txt`.
4. Restart server.
5. Restart agents.
6. Verify API health and SUT status.

When upgrading from a version whose output root was `/root/irqlens`, either keep `IRQLENS_OUTPUT_DIR=/root/irqlens` or accept that earlier captures stay listed but cannot be downloaded; deleting one then removes only its record. Agents older than 1.2.0 keep working but send no storage, PCIe, or softnet telemetry.

## 8. Backup
Minimum backup set:
- SQLite DB file (`IRQLENS_DB_PATH`)
- Output root (`IRQLENS_OUTPUT_DIR`)
- Environment/service configuration used to launch the server; `.env` files are not loaded automatically

## 9. Cleanup and Retention
Current implemented behavior:
- Time-based telemetry retention: each SUT keeps the last `IRQLENS_RETENTION_MINUTES` (default 30) in every time-series table, measured from that SUT's newest sample. IRQ, CPU, or interface count does not shorten the window.
- Pruning runs with every sampling tick or agent telemetry POST, inside the same transaction as the inserts.
- Samples inside a running capture's window are kept until the capture stops.
- The newest tick of a SUT that stopped reporting is kept until that SUT reports again.

Not cleaned up automatically:
- Capture directories, ZIP archives, and session records
- History of SUTs that never report again (at most one retention window each)

Manual cleanup options:
- Delete captures from Collect Logs, or remove old session directories/archives under output root
- Stop the server and delete the database to discard all telemetry history and session records

## 10. Troubleshooting Quick Ops
- SUT stale/offline: check agent process, token, and network route
- No data: verify `/proc` and `/sys` readability on SUT
- Validation: run `tools/validate_sut_visualization.py` with appropriate mode

## 11. Access Control
- `python3 run_irqlens.py` listens on loopback and needs no token. `--remote` requires one: pass `--token`, set `IRQLENS_TOKEN`, or copy the generated token from the startup output.
- `IRQLENS_TOKEN` protects the dashboard, API, downloads, WebSocket, and agents. `IRQLENS_API_TOKEN` and `IRQLENS_AGENT_TOKEN` are optional per-surface overrides; if only one is set, it protects both surfaces.
- Browser sign-in: username `irqlens`, password equal to the token.
- Browser WebSockets use a signed 12-hour HttpOnly session cookie issued at sign-in. It is SameSite=Strict, Secure over HTTPS, and invalidated by token rotation.
- Use separate token values to keep agent credentials out of management APIs. Tokens are shared credentials, not user accounts or per-SUT identities.
- The validator accepts `--token`, `IRQLENS_API_TOKEN`, `IRQLENS_AGENT_TOKEN`, or `IRQLENS_TOKEN`; the agent accepts `--token`, `IRQLENS_AGENT_TOKEN`, or `IRQLENS_TOKEN`.
- Use TLS outside loopback and preserve Authorization and WebSocket upgrades through any proxy. Only `/health` remains public when authentication is configured.
- No token configured disables authentication. Restart after changing credentials.

## 12. Remote Commands
- Upgrade agents to 1.1.0 for command capture and to 1.2.0 for the `storage` category (`lsblk`, `nvme`). Jobs use outbound polling; no inbound agent port is required.
- Capture details show queued/running/completed/failed jobs. Keep the agent running until jobs finish.
- A failed job retains command stdout/stderr and exit codes. Check missing tools, privileges, timeout, and output-limit messages.
- ZIP/report downloads include data currently available. Generate/download again after late results arrive; report status is cleared when results change.
- Automatic session/artifact cleanup and recovery of capture auto-stop timers after server restart remain unimplemented.
