# IRQLENS Operations Guide

## 1. Start and Stop Server

### Start from repository root
```bash
python run_irqlens.py
```

### Start backend explicitly
```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

### Stop
- Press `Ctrl+C` in the server terminal.

## 2. Start and Stop Agent

### Start on Linux SUT
```bash
python3 agent/main.py \
  --server http://<server>:8080 \
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
curl -s http://127.0.0.1:8080/api/health
curl -s http://127.0.0.1:8080/api/systems
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
- Row count trimming applies to IRQ, SoftIRQ, network, interface, and system samples per SUT, not yet CPU utilization/topology or heartbeats.
- Remote diagnostic jobs are persisted in SQLite and reclaimable after a five-minute lease expires.

## 6. Session Files and Downloads
- Output root: `IRQLENS_OUTPUT_DIR` (default `/root/irqlens`)
- Session files endpoint: `/api/sessions/{session_id}/files`
- Session archive endpoint: `/api/sessions/{session_id}/download`

## 7. Upgrading
Suggested process:
1. Stop server and agents.
2. Pull repository updates.
3. Reinstall/update dependencies: `pip install -r backend/requirements.txt`.
4. Restart server.
5. Restart agents.
6. Verify API health and SUT status.

## 8. Backup
Minimum backup set:
- SQLite DB file (`IRQLENS_DB_PATH`)
- Output root (`IRQLENS_OUTPUT_DIR`)
- Environment/service configuration used to launch the server; `.env` files are not loaded automatically

## 9. Cleanup and Retention
Current implemented behavior:
- Telemetry row retention by count (`IRQLENS_METRIC_RETENTION`)

Configured but not currently auto-enforced:
- `IRQLENS_RETENTION_DAYS`
- `IRQLENS_MAX_SESSIONS`
- `IRQLENS_MAX_STORAGE_MB`

Manual cleanup options:
- Remove old session directories/archives under output root
- Archive/rotate DB externally if needed

## 10. Troubleshooting Quick Ops
- SUT stale/offline: check agent process, token, and network route
- No data: verify `/proc` and `/sys` readability on SUT
- Validation: run `tools/validate_sut_visualization.py` with appropriate mode

## 11. Access Control
- Set `IRQLENS_API_TOKEN` for dashboard/API access and `IRQLENS_AGENT_TOKEN` for agents/legacy ingestion. If only one is set, it protects both surfaces.
- Browser sign-in: username `irqlens`, password equal to the API token (or agent token when it is the only one configured).
- Browser WebSockets use a signed 12-hour HttpOnly session cookie issued at sign-in. It is SameSite=Strict, Secure over HTTPS, and invalidated by token rotation.
- Use separate token values to keep agent credentials out of management APIs. Tokens are shared credentials, not user accounts or per-SUT identities.
- The validator accepts `--token` or `IRQLENS_API_TOKEN`; the legacy collector accepts `--token` or `IRQLENS_AGENT_TOKEN`.
- Use TLS outside loopback and preserve Authorization and WebSocket upgrades through any proxy. Only `/health` remains public when authentication is configured.
- Both tokens empty disables authentication. Restart after changing credentials.

## 12. Remote Commands
- Upgrade agents to 1.1.0 for command capture. Jobs use outbound polling; no inbound agent port is required.
- Capture details show queued/running/completed/failed jobs. Keep the agent running until jobs finish.
- A failed job retains command stdout/stderr and exit codes. Check missing tools, privileges, timeout, and output-limit messages.
- ZIP/report downloads include data currently available. Generate/download again after late results arrive; report status is cleared when results change.
- Automatic session/artifact cleanup and recovery of capture auto-stop timers after server restart remain unimplemented.
