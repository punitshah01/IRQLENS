# Changelog

All notable documentation and implementation highlights are tracked here.

## Unreleased
### Added
- Storage & PCIe view: per-device IOPS, throughput, await and queue depth from `/proc/diskstats`, block-device and NVMe metadata, PCIe card enumeration with link-downgrade and AER flags; `GET /api/systems/{sut_id}/io`.
- `/proc/net/softnet_stat` deltas (processed, dropped, time_squeeze, cpu_collision), per CPU and total.
- NIC-to-IRQ mapping from `/sys/class/net/<iface>/device/msi_irqs/` with label matching as fallback; NVMe IRQs classified as storage.
- Cross-NUMA IRQ detection in the IRQ API, Overview findings, IRQ table, CSV, and workbook.
- `irqbalance` state in the header, system telemetry, and capture metadata.
- One multi-sheet Excel workbook per capture (`<session-name>.xlsx`) and `GET /api/sessions/{session_id}/workbook`.
- `POST /api/sessions` for scripted captures, with curl and Python examples in the README.
- `storage` and `pcie` capture categories; `lsblk -J`, `nvme list -o json`, `nvme smart-log`, `ethtool -l`, and `ethtool -x` in the command allowlist.
- Header SUT switcher with status, Pause/Resume Live Updates, IRQ heatmap Top-N selector (25 / 50 / 100 / All) and per-NUMA / per-socket aggregation.
- `run_irqlens.py --remote`, `--token`, `--host`, `--port`; single shared token `IRQLENS_TOKEN`.
- `IRQLENS_RETENTION_MINUTES` and `IRQLENS_STORAGE_INCLUDE_VIRTUAL`; agent `--include-virtual-disks`.
- Capture-quality checks with frozen sampling configuration, boundary-gap and coverage analysis, per-interface checks, and artifact hashes.
- Check Evidence action, authenticated quality endpoint, and explicit evidence status in capture details and HTML reports.
- Optional dashboard/API and agent authentication, including downloads and WebSockets.
- Persisted remote diagnostic jobs with agent polling, lease recovery, bounded allowlisted execution, and status/output reporting.
- Cached IPv4/IPv6 interface discovery through `ip -j address`.
- Session TXT exports and individual raw command output files.
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
- The launcher binds `127.0.0.1:10000` without authentication by default; remote access requires `--remote` and a token. `backend/run_backend.sh` and `.ps1` bind loopback too.
- Default `IRQLENS_OUTPUT_DIR` is `./captures` in the repository root instead of `/root/irqlens`. Captures recorded under another directory stay listed but are not downloadable; deleting one removes only its record.
- SQLite runs in WAL mode and each sampling tick is one transaction.
- Retention is time-based (`IRQLENS_RETENTION_MINUTES`, default 30) and applies to every time-series table, replacing the 5000-row cap that truncated history on high-IRQ servers. Existing history older than the window is dropped on the first sample after upgrading.
- Only IRQ lines that fired during a tick are stored; `cpu_rates` keys are real CPU ids; `/api/irq/current` returns the newest tick only.
- JSON exports are strict (`NaN`/`Infinity` become `null`) and wrapped in a `schema_version` envelope: `data` now holds what used to be the top-level object. CSV exports are UTF-8 with BOM and flattened. All artifacts are written atomically.
- Download endpoints send exact `Content-Type` and `Content-Disposition`; the dashboard downloads through `fetch` + `Blob` and reports HTTP errors.
- ECharts 5.5.1 is vendored under `frontend/static/`; the dashboard makes no external requests.
- Charts redraw at most once per second. The IRQ heatmap shows the Top 25 active IRQs by default.
- Agent version 1.2.0 sends softnet, storage, and PCIe telemetry and up to 1024 IRQ rows.
- The launcher skips `pip install` when dependencies are already importable.
- Python 3.14 is supported and tested in CI: FastAPI 0.121.2, Starlette 0.49.3 (now pinned), uvicorn 0.38.0, and pydantic 2.12.5 replace pins whose `pydantic-core` had no Python 3.14 wheel. The launcher installs wheels only and reports an unsupported interpreter instead of starting a Rust build.
- New captures export only selected time-series categories; repeated stop requests preserve the original capture window and report metadata is refreshed for downloads.
- Escaped untrusted Overview telemetry text and exposed remote-command status in Collect Logs.
- Corrected documentation for current navigation, export locations, security configuration, and operational limitations.
- Kept project licensing explicitly pending owner approval.
- README rewritten to reflect implemented server, agent, diagnostics, visualization, and operations behavior.

### Security
- The token check used the URL that Starlette rebuilds from the `Host` header, so a request with a crafted `Host` could reach every endpoint without the token. Access control now uses the routed request path.
- Request paths containing a backslash and file paths on another drive or UNC share are refused before any filesystem lookup (on Windows they could make the server contact a named host).

### Removed
- XML exports. Captures made earlier keep their XML files.
- `collector/irq_collector.py`, `POST /api/irq/ingest`, `IRQLENS_ALLOWED_INGEST_IPS`, `IRQLENS_DISABLE_INGEST_ALLOWLIST`, and the launcher's `--collect-local`, `--nic`, `--sut-ip`, `--topn` options. Local collection is built into the server; remote hosts use `agent/main.py`.
- `IRQLENS_METRIC_RETENTION` and the never-enforced `IRQLENS_RETENTION_DAYS`, `IRQLENS_MAX_SESSIONS`, `IRQLENS_MAX_STORAGE_MB`.
- CDN dependency on `cdn.jsdelivr.net`.

### Notes
- Repository has no tagged releases at time of writing.
- Historical release versions were not reconstructed retroactively.
