# IRQLENS Real-System Validation Test Plan

## Objective
Validate IRQLENS end-to-end with a real Linux SUT, real backend process, real SQLite persistence, live API/WebSocket connectivity, real frontend rendering path, and real diagnostics/export artifacts.

## Scope and Environment
- Workspace: IRQLENS
- SUT: REDACTED-SUT (REDACTED-IP)
- SUT OS: CentOS Stream 9, Linux 6.14.0-cwf
- Backend: FastAPI on port 8080 (remote host)
- Access path for QA: local tunnel http://127.0.0.1:18080
- Agent mode: remote agent posting register/heartbeat/telemetry
- Data store: backend/data/irqlens.db

## Entry Criteria
- SSH access to SUT works.
- Python virtual environment exists on SUT and backend requirements are installed.
- Backend process is reachable on 127.0.0.1:8080 from SUT.
- Agent process is launchable on SUT.

## Exit Criteria
- Core functional tests pass for registration, heartbeat, ingest, query, diagnostics session, and export download.
- Any non-passing test is documented with root cause and next action.

## Test Matrix
| Test ID | Area | Test Action | Expected Result | Evidence Artifact |
|---|---|---|---|---|
| T001 | Environment | Verify SUT hostname, kernel, distro | Real Linux SUT identity collected | test-results/liveqa/environment/* |
| T002 | Backend startup | Backend process starts and serves root | HTTP 200 on / | test-results/liveqa/http_root_status.txt |
| T003 | Health API | GET /api/health | ok=true, db=ok, websocket connected | test-results/liveqa/api_health.json |
| T004 | Systems registry | GET /api/systems | local and remote systems listed | test-results/liveqa/api_systems.json |
| T005 | Agent registration | Remote agent registers SUT | remote SUT exists and ONLINE | test-results/liveqa/api_systems.json |
| T006 | Heartbeat flow | Agent heartbeats refresh last_seen | last_seen updates and status ONLINE | test-results/liveqa/api_systems.json |
| T007 | IRQ ingest/query | GET /api/irq/current?sut_id=... | Non-empty IRQ rows from remote SUT | test-results/liveqa/api_irq_remote.json |
| T008 | SoftIRQ ingest/query | GET /api/softirq/current?sut_id=... | SoftIRQ sample object returned | test-results/liveqa/api_softirq_remote.json |
| T009 | Network ingest/query | GET /api/network/current?sut_id=... | Interface rows and non-zero rates | test-results/liveqa/api_network_remote.json |
| T010 | Interface inventory | GET /api/interfaces?sut_id=... | Remote interfaces discovered | test-results/liveqa/api_interfaces_remote.json |
| T011 | 404 handling | GET /api/systems/no-such-sut | HTTP 404 | test-results/liveqa/http_notfound_status.txt |
| T012 | Diagnostics start | POST /api/sessions/start | Session created with running status | test-results/liveqa/session_start_response.json |
| T013 | Diagnostics stop | POST /api/sessions/{id}/stop | Session transitions to stopped | test-results/liveqa/session_stop_response.json |
| T014 | Session files | GET /api/sessions/{id}/files | Export metadata lists generated files | test-results/liveqa/session_20260811-081149-755_files.json |
| T015 | Export download | GET /api/sessions/{id}/download | Zip file downloaded, non-zero bytes | test-results/liveqa/session_20260811-081149-755.zip |
| T016 | Database persistence | Verify SQLite db file | Database file exists and non-zero size | backend/data/irqlens.db |
| T017 | WebSocket handshake | Open ws://.../ws | WebSocket reaches Open state | terminal evidence in QA run |
| T018 | Visualization API | GET /api/systems/{sut}/visualization | Time-window payload populated | test-results/liveqa/api_visualization_remote.json |
| T019 | Topology API | GET /api/systems/{sut}/visualization/topology | Remote topology available or explicit reason | test-results/liveqa/api_topology_remote.json |
| T020 | Automated tests | Run repository test suite | Baseline tests pass in configured env | terminal evidence in QA run |

## Status Legend
- PASS: Behavior matches expected result with direct evidence.
- FAIL: Behavior contradicts expected result.
- WARN: Works partially or with environment limitation.
- BLOCKED: Could not execute due external blocker.

## Execution Notes
- Record exact command/action used for each test.
- Capture expected vs actual output for every non-PASS item.
- Preserve artifacts under test-results/liveqa.
