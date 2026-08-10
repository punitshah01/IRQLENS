# IRQLENS Remote SUT Implementation Plan

## Scope
Upgrade IRQLENS to support remote Linux SUT monitoring via lightweight SUT agents while preserving current local live monitoring, existing endpoints, dashboard behavior, and export/session flows.

## Phase 1 Existing application analysis
- Verify current tests and startup behavior.
- Inventory existing collectors, API, WebSocket, storage, and frontend navigation.
- Identify compatibility constraints for existing local collector ingest flow.

## Phase 2 Agent extraction/refactoring
- Reuse existing collector logic by introducing shared telemetry payload models for server+agent.
- Add `agent/` package with startup, registration, telemetry loop, diagnostics runner, and reconnect logic.
- Keep `collector/irq_collector.py` compatibility path intact.

## Phase 3 Agent-server protocol
- Define Pydantic message models:
  - registration
  - heartbeat
  - telemetry
  - diagnostics request/response metadata
- Add token-based authentication (`Authorization: Bearer <token>`).
- Add protocol/version field for compatibility checks.

## Phase 4 SUT registration
- Add server-side system registry table and APIs.
- Support manual system creation and agent self-registration/update.
- Persist host metadata: hostname, os, kernel, cpu/mem, interfaces, ip addresses, agent version.

## Phase 5 Heartbeat
- Add heartbeat endpoint and status manager.
- Track `last_seen`, compute ONLINE/OFFLINE/STALE/CONNECTING/ERROR.
- Configurable stale threshold.

## Phase 6 Remote telemetry
- Add agent telemetry endpoint to ingest per-SUT IRQ/SoftIRQ/network/system snapshots.
- Route all records by `sut_id` and persist with SUT association.
- Preserve local sampler mode as built-in local SUT source.

## Phase 7 Multi-SUT support
- Add system selector APIs and server-side filtering.
- Ensure all dashboard data endpoints can filter by `sut_id`.
- Keep legacy host-based endpoints functional.

## Phase 8 Remote diagnostics
- Add remote diagnostics trigger endpoint:
  - browser -> server -> agent assignment queue
- Agent executes allowlisted commands only and uploads structured results.
- Preserve local diagnostics functionality.

## Phase 9 Storage changes
- Extend SQLite schema (non-destructive migration):
  - `systems`
  - telemetry tables include `sut_id`
  - `agent_heartbeats`
  - `remote_sessions`
- Add migration helpers for existing DB files.

## Phase 10 UI changes
- Add Systems page and top global system selector.
- Add connection status, stale marker, and source mode indicator (Local vs Remote Agent).
- Ensure selector updates all telemetry pages and interface dropdown.

## Phase 11 Authentication/security
- Token-based agent auth via env config.
- Command allowlist enforced server+agent side.
- Prevent arbitrary command execution and path traversal.
- Keep file download constrained to output directories.

## Phase 12 Testing
- Add tests for:
  - registration/auth/heartbeat
  - telemetry routing per sut_id
  - multi-SUT isolation
  - stale/offline status logic
  - export includes sut_id/session metadata

## Phase 13 End-to-end validation
- Validate local mode still works.
- Validate remote mode with one agent and one browser session.
- Validate multi-SUT behavior with at least simulated second SUT dataset.

## Phase 14 Documentation
- Update README with server/agent setup, auth tokens, API changes, local+remote mode, troubleshooting.

## Acceptance Criteria
- Existing local mode and tests remain green.
- Remote SUTs register and stream telemetry with per-SUT isolation.
- UI can select SUT and show correct data.
- Heartbeat and stale/offline status visible.
- Remote diagnostics execute through agent allowlist and export correctly.
