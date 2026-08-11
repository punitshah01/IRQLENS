# IRQLENS Redesign Test Report

## Summary
This report covers the UX redesign and functional correction pass for IRQLENS against a real Linux SUT and live backend path.

## UX Issues Fixed
- Replaced the chart-wall layout with a task-first information architecture: Systems, Overview, IRQ, Network, CPU, Diagnostics, Sessions.
- Added persistent SUT context in the header with SUT identity, status, last update, breadcrumb, and shared time range.
- Moved time range controls out of chart plotting areas.
- Reworked CPU analysis into a topology-first CPU / NUMA map with CPU detail drill-down.
- Reworked Network into an interface-driven investigation flow with current traffic, history, packet statistics, metadata, and related IRQs.
- Reworked Diagnostics and Sessions into an evidence-collection workflow instead of raw controls and tables.

## Functional Bugs Fixed
- Systems -> Open Dashboard now selects the SUT, loads telemetry/history/topology, and navigates to Overview.
- Frontend assets are now served correctly by the backend via static file mounting.
- Interface-specific network history now uses the live per-interface endpoint correctly.
- CPU metric selection now updates the visible selected state and heatmap presentation correctly.
- Interface history rows are time-sorted before charting.

## UI Components Removed or Moved
- Removed Logs from the primary workflow.
- Removed Compare from the primary workflow.
- Removed standalone Interfaces page.
- Moved advanced operational/backend controls into Settings.
- Moved secondary analysis out of Overview where it did not answer immediate user questions.

## UI Components Redesigned
- Overview
- Systems
- IRQ investigation
- Network investigation
- CPU topology and detail view
- Diagnostics capture workflow
- Sessions review workflow

## New Visualizations and UX Structures
- Topology-based CPU / NUMA map using real SUT topology data.
- Focused findings list driven by deterministic current-state conditions.
- Interface-ranked network list with interface-specific detail flow.
- Session detail panel with grouped downloadable files.

## Data Flow Changes
- Frontend split from one inline file into structured HTML, CSS, and JS assets.
- Backend now serves `/frontend/*` static assets directly.
- Selected SUT is persisted and reused across page navigation.
- Selected interface and selected CPU are preserved in the frontend state model.

## Tests Executed
| Test ID | Area | Expected | Actual | Status |
|---|---|---|---|---|
| UX-01 | Systems -> Open Dashboard | Selecting a system opens the dashboard for that SUT | Code path now selects host, refreshes SUT data, and switches to Overview | PASS |
| UX-02 | Frontend asset serving | HTML, CSS, and JS load from live backend | `/`, `/frontend/styles.css`, and `/frontend/app.js` all returned 200 | PASS |
| UX-03 | Remote telemetry continuity | Remote SUT remains ONLINE after redesign deploy | Remote SUT remained ONLINE and queryable | PASS |
| UX-04 | CPU topology | Topology endpoint provides real data or clear unavailability | Remote topology returned `available=true` with 288 rows | PASS |
| UX-05 | Interface selection backend path | Per-interface network history is available for a live NIC | `/api/network/ens6f0?sut_id=...` returned 833 samples | PASS |
| UX-06 | Diagnostics workflow | Start/stop capture still works after redesign | Session created and stopped successfully with 20 files | PASS |
| UX-07 | Sessions file listing | Session files remain downloadable and grouped | Session file listing returned 20 files | PASS |
| UX-08 | Automated tests | Existing automated suite still passes | Remote project environment: `10 passed` | PASS |

## Tests Passed
- 8

## Tests Failed
- 0

## Remaining Limitations
- CPU load is not currently exposed in the frontend telemetry model, so CPU page metric switching supports IRQ and SoftIRQ, while CPU Load is shown as unavailable rather than fabricated.
- This validation pass confirmed live asset serving, API behavior, topology availability, per-interface history, sessions, and automated tests, but did not include browser-automation capture of every click path.
- SoftIRQ class richness still depends on what the live SUT exposes in the captured window.

## Evidence and Validation Notes
- Live backend/tunnel checks confirmed HTML shell markers for `Systems`, `Right Now`, `Top IRQ Sources`, `Interface:`, `Capture Evidence`, and `Diagnostic Sessions`.
- Remote automated tests were executed with `.venv/bin/python -m pytest -q` and passed.
- All previously captured QA evidence remains under `test-results/liveqa`.
