# IRQLENS Real-System Test Report

## Summary
This report validates IRQLENS against a real Linux SUT and live backend path (agent -> API -> SQLite -> query/export), with evidence captured under test-results/liveqa.

## Test Environment
- Date: 2026-08-11
- SUT: FL31CA106KS1002 (10.45.154.35)
- SUT OS: CentOS Stream 9, kernel 6.14.0-cwf
- Backend endpoint used for validation: http://127.0.0.1:18080 (SSH tunnel to remote 8080)
- Data store: backend/data/irqlens.db

## Results
| Test ID | Area | Expected | Actual | Status | Evidence |
|---|---|---|---|---|---|
| T002 | Backend startup | Root endpoint reachable | HTTP 200 returned from / | PASS | test-results/liveqa/http_root_status.txt |
| T003 | Health API | App healthy, DB healthy, WS connected | ok=true, database_status=ok, websocket_status=connected | PASS | test-results/liveqa/api_health.json |
| T004 | Systems registry | Local + remote systems listed | systems count=2 with local and sut-fl31ca106ks1002 | PASS | test-results/liveqa/api_systems.json |
| T005 | Agent registration | Remote SUT registered | sut-fl31ca106ks1002 present | PASS | test-results/liveqa/api_systems.json |
| T006 | Heartbeat flow | Remote status ONLINE with current last_seen | status ONLINE with refreshed timestamp | PASS | test-results/liveqa/api_systems.json |
| T007 | IRQ ingest/query | Non-empty remote IRQ rows | /api/irq/current returned 200 rows (limit 200) | PASS | test-results/liveqa/api_irq_remote.json |
| T008 | SoftIRQ ingest/query | SoftIRQ sample returned | sample object returned; class rates currently empty in captured snapshot | WARN | test-results/liveqa/api_softirq_remote.json |
| T009 | Network ingest/query | Interface metrics with rates | interfaces=6, RX/TX rates non-zero | PASS | test-results/liveqa/api_network_remote.json |
| T010 | Interface inventory | Remote interfaces listed | 6 interfaces returned | PASS | test-results/liveqa/api_interfaces_remote.json |
| T011 | 404 handling | Unknown system returns 404 | NOT_FOUND_STATUS=404 | PASS | test-results/liveqa/http_notfound_status.txt |
| T012 | Diagnostics start | Session starts as running | session status=running | PASS | test-results/liveqa/session_start_response.json |
| T013 | Diagnostics stop | Session transitions to stopped | status=stopped with end_time set | PASS | test-results/liveqa/session_stop_response.json |
| T014 | Session files | Export records generated | files count=20 across json/csv/xml/txt categories | PASS | test-results/liveqa/session_20260811-081149-755_files.json |
| T015 | Export download | Session download produces zip | zip created, size 104667 bytes | PASS | test-results/liveqa/session_20260811-081149-755.zip |
| T016 | Database persistence | SQLite file exists and is non-empty | backend/data/irqlens.db exists, size 258048 bytes | PASS | backend/data/irqlens.db |
| T017 | WebSocket handshake | WS endpoint accepts client | ClientWebSocket reached Open state on ws://127.0.0.1:18080/ws | PASS | terminal QA command output |
| T018 | Visualization API | Visualization payload populated for remote SUT | API responded but history/hotspot arrays remained empty in repeated checks | WARN | test-results/liveqa/api_visualization_remote.json |
| T019 | Topology API | Remote topology available | API responded with no topology nodes/links for remote SUT in current run | WARN | test-results/liveqa/api_topology_remote.json |
| T020 | Automated tests | Repo tests executable in current shell | pytest not installed/available in local shell and local Python has no pytest module | BLOCKED | terminal QA command output |

## Aggregate Status
- PASS: 14
- WARN: 3
- FAIL: 0
- BLOCKED: 1

## Key Findings
1. Real remote telemetry path is functioning: remote agent data is reaching backend and query endpoints (IRQ, network, interfaces).
2. Diagnostics and export pipeline is functioning end-to-end, including downloadable archive output.
3. Visualization/topology endpoints for remote SUT return structurally valid responses but no populated remote history/topology in this capture window.
4. Local shell lacks pytest tooling, preventing in-shell automated test execution as part of this live run.

## Known Gaps and Next Actions
1. Visualization/topology for remote path: inspect backend data-to-visualization mapping for remote SUT IDs and CPU topology ingestion timing.
2. SoftIRQ rate coverage: verify remote collector cadence and whether non-empty per_class rates require longer runtime windows.
3. Test automation reproducibility: install pytest in active local environment or run tests from project venv to remove BLOCKED status.

## Evidence Location
All captured artifacts for this run are in test-results/liveqa.
