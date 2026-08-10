# IRQLENS Implementation Plan

## Goal
Implement a production-quality Linux IRQ and network diagnostics dashboard on SUTs with real telemetry, sessionized diagnostic collection, export formats (JSON/CSV/XML/TXT), historical storage, robust API/WebSocket streaming, and professional UI.

## Repository Analysis Summary

### Current architecture
- Backend: FastAPI app in `backend/app/main.py`.
- Storage: SQLite helper in `backend/app/store.py`.
- Models: basic Pydantic models in `backend/app/models.py`.
- WebSocket: simple broadcast manager in `backend/app/ws.py`.
- Collector: single Linux collector in `collector/irq_collector.py` pushing samples to backend.
- Frontend: single-page static UI in `frontend/index.html`.
- Startup: root launcher `run_irqlens.py` and backend scripts.

### Existing functionality (working)
- Live IRQ ingest from Linux `/proc/interrupts` and rate calculations (collector-side delta).
- SoftIRQ and `/proc/net/dev` parsing in collector.
- Host summary and latest history queries in backend.
- Basic WebSocket trigger-based refresh.
- SQLite persistence with per-host row-retention count.
- Functional browser dashboard with overview, IRQ detail, and some interface/command previews.

### Missing functionality vs required spec
- Missing architecture modularity (collector responsibilities are monolithic and partly mixed with artifact export).
- No backend-native telemetry collection service for local SUT mode.
- No explicit session lifecycle APIs (`start/stop/list/files/download/delete`).
- No strict `/root/irqlens/` session package workflow.
- No generalized export engine for all major categories in JSON/CSV/XML/TXT.
- No diagnostics page with category toggles and progress model.
- No full network diagnostics coverage (routes, neigh, sockets, sysctl/proc net stats, robust ethtool capture metadata).
- No full system diagnostics model (OS distro/version, uptime, NUMA, root status, dependencies map).
- No retention policy by days/session count/storage MB.
- Health endpoint is minimal; no collector/database/ws/dependency status payload.
- API endpoint set does not match target breadth.
- UI information architecture is incomplete (no sidebar and required pages set).
- No tests currently in repository.
- README does not document required architecture and validation for Ubuntu/CentOS behavior.

### Duplicate/obsolete areas to rationalize
- Export writing currently done in collector with mixed startup and periodic artifacts; must move to dedicated export/persistence/session layer.
- Collector currently executes startup command bundle but without robust command discovery and structured results per command invocation.
- Backend route naming currently centered on ingest pattern; will need versioned REST semantics and endpoint expansion.

## Required Target Architecture (Implementation Mapping)

- `backend/app/config.py`: centralized typed configuration with defaults + env parsing.
- `backend/app/models.py`: expanded typed domain models for system/network/irq/session/export/logging.
- `backend/app/collectors/`
  - `base.py`
  - `irq.py`
  - `softirq.py`
  - `network.py`
  - `interface.py`
  - `ethtool.py`
  - `system.py`
  - `commands.py`
- `backend/app/services/`
  - `sampler.py` (high-frequency telemetry scheduler)
  - `diagnostics.py` (on-demand snapshot/session orchestrator)
  - `processor.py` (delta/rate/normalization)
  - `exporter.py` (JSON/CSV/XML/TXT)
  - `retention.py` (days, sessions, storage)
  - `health.py` (dependency/root/runtime status)
- `backend/app/store.py`: schema upgrade for telemetry history and session metadata.
- `backend/app/ws.py`: typed event topics and status heartbeat.
- `backend/app/main.py`: API routes, lifecycle startup/shutdown, static frontend hosting.
- `frontend/index.html`: multi-page dashboard shell (sidebar sections) with real-time views.
- `frontend` optional split JS/CSS modules if needed for maintainability.
- `tests/`: parser/rate/export/API coverage.

## Phase-by-Phase Implementation Plan

## Phase 1: Repository and architecture cleanup
- Create `plans/IRQLENS_IMPLEMENTATION_PLAN.md` (this file).
- Introduce package layout for collectors/services without breaking startup.
- Preserve existing ingest path while adding new internal service interfaces.

## Phase 2: Core models and config
- Expand Pydantic models:
  - `SystemInfo`, `InterfaceInfo`, `IRQSample`, `IRQRate`, `SoftIRQSample`, `NetworkSample`, `DiagnosticCommandResult`, `CollectionSession`, `ExportFile`, `HealthStatus`.
- Add centralized configuration fields:
  - host, port, collection interval, db path, output directory, retention days, max sessions, max storage MB, log level, command timeouts.
- Add root privilege detection + dependency checks.

## Phase 3: IRQ collector
- Implement robust `/proc/interrupts` parsing.
- Collect affinity from `/proc/irq/*` and optional `/sys/kernel/irq` hints.
- Handle dynamic CPUs and changing IRQ lines.

## Phase 4: SoftIRQ collector
- Implement `/proc/softirqs` parser with total and per-CPU classes.
- Delta/rate computation resilient to resets and elapsed-time jitter.

## Phase 5: Network/interface collector
- Dynamic interface discovery from `/sys/class/net` + `/proc/net/dev` fallback.
- Per-interface stats + global aggregate stats.
- Interface metadata: state, mtu, mac, speed, duplex, driver, IPv4/IPv6.
- Correlate probable network IRQs from names and sysfs mapping where available.

## Phase 6: System collector
- Hostname, kernel, os-release parsing, uptime, loadavg, cpu/memory info, NUMA summary.

## Phase 7: Diagnostic command collector
- Safe command registry with discovery and timeout.
- Read-only commands only; no user-provided command execution.
- Structured command result capture: command, timestamp, exit_code, stdout, stderr.
- Conditional ethtool per interface.

## Phase 8: Export engine
- Generate JSON/CSV/XML/TXT for categories: IRQ, SoftIRQ, Network, System, Commands metadata.
- Validate real CSV/XML serialization, not extension renaming.

## Phase 9: SQLite history and retention
- Telemetry tables for irq/softirq/network/interface/system.
- Session tables and status.
- Retention enforcement by days, max sessions, max storage MB.

## Phase 10: REST API expansion
- Implement endpoints:
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
  - `DELETE /api/sessions/{session_id}`

## Phase 11: WebSocket streaming
- Typed live message events:
  - status, system, irq, softirq, cpu, network, interfaces, diagnostics_progress.
- Reconnect-aware status indicators.

## Phase 12: Frontend dashboard
- Professional dark monitoring UI with sidebar pages:
  - Overview, IRQ Monitor, SoftIRQ, CPU, Network, Interfaces, Diagnostics, Sessions, Logs, Settings.
- Real-time charts, sortable/filterable tables, pagination, interface selector, stale-data indicators.

## Phase 13: Session management
- Start/stop diagnostics with category selection.
- Persist under `/root/irqlens/sessions/<session-id>/...` with `latest` pointer/symlink equivalent.
- Package session archive for download.

## Phase 14: Tests
- Parser tests for `/proc/interrupts`, `/proc/softirqs`, `/proc/net/dev`.
- Rate edge-case tests (reset/new/disappeared/delta jitter).
- Export format tests.
- API tests for major endpoints.
- WebSocket message-format sanity tests.

## Phase 15: Documentation
- Rewrite README with architecture, setup, permissions, usage, API, troubleshooting, export/session workflow.

## Phase 16: End-to-end validation checklist
- Verify real SUT values against `/proc` and command outputs.
- Verify session creation, files, archive, and UI behavior under missing-command conditions.

## File-by-File Change Plan
- `backend/app/config.py`: replace with typed config + validation.
- `backend/app/models.py`: expand schemas.
- `backend/app/store.py`: schema redesign + migration-safe creation.
- `backend/app/ws.py`: channel-aware manager.
- `backend/app/main.py`: new API routes and app lifecycle.
- `collector/irq_collector.py`: convert to optional remote agent publisher mode compatible with new APIs.
- `frontend/index.html`: rebuild dashboard IA and real-time UX.
- `run_irqlens.py`: startup dependency checks, OS/root visibility, service start orchestration.
- `README.md`: comprehensive docs.
- `backend/requirements.txt`: add testing/runtime deps (minimal required).
- Add new folders/files under `backend/app/collectors`, `backend/app/services`, `tests`, `plans`.

## Testing Strategy
- Unit tests run with pytest.
- FastAPI endpoint tests via `TestClient`.
- Isolated temporary directories for SQLite/output/session artifacts.
- Fixture-based proc/net samples to avoid requiring root or Linux-specific CI features.
- Command execution mocked in tests for deterministic behavior.

## Completion Checklist
- [x] Repository analyzed.
- [x] Plan document created.
- [x] Modular collectors implemented.
- [x] Delta/rate engine validated with tests.
- [x] Full diagnostics command collector with safety/timeouts.
- [x] Session lifecycle APIs and persistence.
- [x] `/root/irqlens/` output structure generation.
- [x] JSON/CSV/XML/TXT exports for major categories.
- [x] REST API endpoint set complete.
- [x] WebSocket real-time feed complete.
- [x] Frontend pages complete and production-quality.
- [x] Tests implemented and passing.
- [x] README complete.
- [ ] E2E Linux SUT validation performed/documented.

## Notes
- Existing ingest flow is useful and will be preserved as an optional mode while introducing backend-native telemetry service.
- Unsupported/missing command or proc/sys sources will produce structured `N/A`/error fields and never abort entire collection.
