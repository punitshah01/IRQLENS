# Changelog

All notable documentation and implementation highlights are tracked here.

## Unreleased
### Added
- Capture-quality checks with frozen sampling configuration, boundary-gap and coverage analysis, per-interface checks, and artifact hashes.
- Check Evidence action, authenticated quality endpoint, and explicit evidence status in capture details and HTML reports.
- Optional dashboard/API and agent authentication, including downloads, WebSockets, and legacy ingestion.
- Persisted remote diagnostic jobs with agent polling, lease recovery, bounded allowlisted execution, and status/output reporting.
- Cached IPv4/IPv6 interface discovery through `ip -j address`.
- Session XML/TXT exports and individual raw command output files.
- Isolated test storage, browser regression tests, and Linux/Windows backend plus Chromium CI workflows.
- Advanced visualization layer with IRQ/CPU/network/SoftIRQ charting and heatmaps.
- Multi-SUT comparison view.
- Remote agent registration, heartbeat, and telemetry ingestion.
- Remote CPU topology ingestion and topology endpoint.
- Custom visualization time ranges (`from_ts`, `to_ts`) and frontend custom-range controls.
- Frontend chart zoom persistence and reset behavior.
- Real-SUT/API validation script: `tools/validate_sut_visualization.py`.
- Dedicated documentation set:
  - `docs/ARCHITECTURE.md`
  - `docs/AGENT_PROTOCOL.md`
  - `docs/VISUALIZATIONS.md`
  - `docs/OPERATIONS.md`
  - `docs/images/README.md`

### Changed
- New captures export only selected time-series categories; repeated stop requests preserve the original capture window and report metadata is refreshed for downloads.
- Escaped untrusted Overview telemetry text and exposed remote-command status in Collect Logs.
- Corrected documentation for current navigation, export locations, security configuration, and operational limitations.
- Kept project licensing explicitly pending owner approval.
- README rewritten to reflect implemented server, agent, diagnostics, visualization, and operations behavior.

### Notes
- Repository has no tagged releases at time of writing.
- Historical release versions were not reconstructed retroactively.
