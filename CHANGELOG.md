# Changelog

All notable documentation and implementation highlights are tracked here.

## Unreleased
### Added
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
- README rewritten to reflect implemented server, agent, diagnostics, visualization, and operations behavior.

### Notes
- Repository has no tagged releases at time of writing.
- Historical release versions were not reconstructed retroactively.
