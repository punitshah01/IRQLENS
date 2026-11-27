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
- `run_irqlens.py --host`, `--port`, `--token`; optional single shared token `IRQLENS_TOKEN`.
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
- The launcher serves `http://<host-ip>:10000` on all interfaces without sign-in by default. `--port` changes the port, `--host 127.0.0.1` limits it to the server host, and `--token` requires a token. `backend/run_backend.sh` and `.ps1` bind loopback.
- Default `IRQLENS_OUTPUT_DIR` is `./captures` in the repository root instead of `/root/irqlens`. Captures recorded under another directory stay listed but are not downloadable; deleting one removes only its record.
- SQLite runs in WAL mode and each sampling tick is one transaction.
- Retention is time-based (`IRQLENS_RETENTION_MINUTES`, default 30) and applies to every time-series table, replacing the 5000-row cap that truncated history on high-IRQ servers. Existing history older than the window is dropped on the first sample after upgrading.
- Only IRQ lines that fired during a tick are stored; `cpu_rates` keys are real CPU ids; `/api/irq/current` returns the newest tick only.
- JSON exports are strict (`NaN`/`Infinity` become `null`) and wrapped in a `schema_version` envelope: `data` now holds what used to be the top-level object. CSV exports are UTF-8 with BOM and flattened. All artifacts are written atomically.
- Download endpoints send exact `Content-Type` and `Content-Disposition`; the dashboard downloads through `fetch` + `Blob` and reports HTTP errors.
- ECharts 5.5.1 is vendored under `frontend/static/`; the dashboard makes no external requests.
- Charts redraw at most once per second. The IRQ heatmap shows the Top 25 active IRQs by default.
- Dashboard refreshes are one round of parallel requests instead of a chain, responses are gzip-compressed, and the dashboard requests `compact=1` visualization data. Trend history is refetched every 1 to 15 s depending on the range, CPU topology and interface details every 30 s, and a hidden tab does not poll. On a 256-CPU SUT 210 ms away a refresh went from 1.6 s (30 s range) and 2.5-3.6 s (5 min range) to 0.3 s and 0.4 s; the 1 h range, which never finished a refresh within 8 s, refreshes in 0.25 s.
- Views, time ranges, and Collect Logs open on the click from the data already loaded; newer data follows. A response for a previous SUT, range, or interface is discarded.
- A redraw no longer swallows a click (navigation and CPU cells), resets the IRQ matrix scroll position, an open interface dropdown, or hidden series and paging in the storage chart legends. The IRQ matrix is recolored in place and the CPU tooltip no longer catches clicks.
- Chart axes use compact labels (`1.8M`, `800KB/s`) that fit beside the plot, time labels no longer overlap, storage axis names sit below the legend, and the IRQ bar chart labels every bar.
- `GET /api/systems/{sut_id}/visualization` accepts `compact=1`, which omits the per-CPU history and cells and the softirq history. The default response is unchanged.
- The page and its scripts are served with `Cache-Control: no-cache`, so a reload after an update runs the new dashboard.
- Capture Detail is folded: checks that need attention are shown, while the full check list and each file group open on click and stay open across refreshes. An unchanged capture is no longer redrawn every second.
- Raw command outputs are named after their command (`026_ethtool_-S_ens3np0.txt` instead of `command_026.txt`). Captures made earlier keep their file names.
- Command output is kept up to 1,048,576 characters per stream instead of 65,536, which cut `ethtool -S` on a 126-queue NIC (110,858 characters) and marked the capture incomplete.
- Device queries that an interface does not support (`ethtool -S lo`, `ethtool -g docker0`: no output and `Operation not supported` or `no stats available`) are listed as `unsupported_commands` in the capture quality instead of as failed checks. On a server with `lo` and `docker0` they were 11 of 12 failed checks. **Check Evidence** applies this to existing captures.
- Agent version 1.2.0 sends softnet, storage, and PCIe telemetry and up to 1024 IRQ rows.
- The launcher skips `pip install` when dependencies are already importable.
- With `--host 127.0.0.1` the launcher states that the URL works on the server host only and prints the SSH port-forward command.
- `/proc/interrupts` without a `CPU0 CPU1 ...` header line (seen on a 7.2 kernel with 256 CPUs) is parsed: the CPU columns come from the online CPU list. Before, the IRQ view showed a single row named after its counters.
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
