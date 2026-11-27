# IRQLENS Visualizations

This document explains chart semantics, data sources, and calculations.

## Data Source Endpoints
Primary visualization payloads:
- `GET /api/systems/{sut_id}/visualization`
- `GET /api/systems/{sut_id}/visualization/topology`
- `GET /api/systems/{sut_id}/io` (storage, PCIe, softnet, irqbalance)
- `GET /api/visualization/compare`

`compact=1` on the visualization endpoint omits `series.cpu_utilization[].cpus`, `series.softirq_classes`, `series.softirq_total`, `irq_heatmap.values`, and `cpu_heatmap.values`. The dashboard uses it: on a 256-CPU SUT those fields are close to 90% of the response before compression. Responses are gzip-compressed for clients that send `Accept-Encoding: gzip`.

Supporting data:
- `GET /api/irq/current`
- `GET /api/softirq/current`
- `GET /api/network/current`
- `GET /api/interfaces`
- `GET /api/summary/current`

## Time Range and Zoom
- Presets: 30s, 1m, 5m, 15m, 30m, 1h
- Custom range: `from_ts` and `to_ts`
- Chart zoom state persists in frontend memory until reset
- `Reset Zoom` clears zoom state and reapplies full ranges

## Machine Map
Shown on Overview when `GET /api/fleet` lists two or more machines. Machines whose status is `OFFLINE` are left out, as is the server's own host when it is not a Linux machine.

- Box: one machine, with average and maximum CPU utilization and total IRQ/s from its newest sample. Clients are drawn on the left, servers on the right. A machine is a client unless it reported `role: server`; the machine the server runs on is the server unless another machine did.
- Pill: one IPv4 network (`192.0.2.0/25`) on which at least two machines have an address. Loopback and link-local addresses are skipped. An address that more than one machine has is a host-only bridge (`docker0`) and is skipped too.
- Line: one interface on that network. Label: link speed from `/sys/class/net/<iface>/speed`, RX and TX in bit/s (the stored rates are bytes per second, times 8), and `utilization_pct = max(rx, tx) * 8 / link speed`.
- Line width grows from 2 px (idle) to 10 px (100% of the link). Blue: at least 1 Mbit/s. Red: drops or errors in the newest sample, or `operstate` is `down`. Dashed: the machine is not `ONLINE`; its numbers are replaced by the time of its last update.
- Selecting a box selects that machine, exactly as the SUT dropdown does.
- A machine the server connects to over SSH is on the map from the moment it is added: `Connecting over SSH` until its first sample, and `Not connected` with the message from `ssh` when the connection fails. Such a machine is also shown when it is the only one, so that the reason is visible.
- Windows narrower than 900 px stack the boxes and leave the lines out.

The map cannot tell a direct cable from a switch: both are two interfaces on one network.

## IRQ Heatmap
- X-axis: CPU IDs, NUMA nodes, or sockets (selector; grouping uses the SUT topology)
- Y-axis: Top 25 active IRQs by default (rate > 0); selector for 25 / 50 / 100 / All
- Cell value: IRQ/sec for the `(irq, column)` pair
- Tooltip includes IRQ source, column, IRQ/sec, total count

Source fields:
- rows of `GET /api/irq/current` (`cpu_rates`, `total_rate`), grouped in the browser
- the visualization payload also carries `irq_heatmap.cpus`, `irq_heatmap.irqs`, `irq_heatmap.values` for the Top N active IRQs (`top_n`, default 25; `0` returns all)

## Cross-NUMA IRQ Flag
- Device node: `/sys/kernel/irq/<irq>/node`, else the owning device's `numa_node`.
- Handler nodes: NUMA nodes of the CPUs that each handle more than 10% of the IRQ's rate.
- `cross_numa_flag` is true when a handler node differs from the device node; `remote_share_pct` is the share handled off-node.
- Shown as an Overview finding and in the Cross-NUMA column of the IRQ table. IRQs without a known device node are not flagged.

## Storage Charts
- IOPS per device: `read_iops`, `write_iops`
- Throughput per device: `read_mb_s`, `write_mb_s` (MiB/s from 512-byte sectors)
- Latency and queue depth: `avg_read_await_ms`, `avg_write_await_ms` (time per completed I/O in the interval) on the left axis, `queue_depth_in_flight` dashed on the right axis
- `util_pct`: share of the interval the device had I/O in progress
- Source: `/proc/diskstats` deltas, `storage.series` in `GET /api/systems/{sut_id}/io`

## PCIe Cards
- One row per NIC, NVMe/storage controller, GPU, or accelerator from `/sys/bus/pci/devices/*`
- Link status shows current vs maximum speed and width; a red `Downgraded` badge marks `current < max` for either
- AER shows correctable / non-fatal / fatal totals; amber when non-zero, red when the total increased since the previous sample

## Redraw Rate
- Charts and tables redraw at most once per 1000 ms regardless of the backend sampling interval.
- Current values (KPIs, CPU and IRQ matrices, tables) are refetched on every refresh. Trend history is refetched once per 1/150 of the selected range: every second up to 2.5 minutes, every 2 s for 5 minutes, every 15 s for an hour. CPU topology and interface details are refetched every 30 s.
- A hidden browser tab does not poll; it refreshes when shown again.
- Pause Live Updates stops reloading and redrawing monitoring views until resumed.

## CPU Heatmap
- X-axis: CPU IDs
- Y-axis: `IRQ/s` and `SoftIRQ/s`
- Cell value: per-CPU rate for selected metric row

Derived from:
- IRQ per-CPU rates aggregated from latest IRQ rows
- SoftIRQ per-CPU rates from latest softirq sample

## CPU Grid
- Alternate CPU load visualization
- CPU rows are arranged into a near-square grid
- Cell intensity is `total_rate = irq_rate + softirq_rate`

## CPU Utilization
- Source: Linux `/proc/stat` cumulative per-CPU counters (`cpu0`, `cpu1`, ...).
- Method: utilization is calculated from deltas between consecutive samples for each logical CPU.
- Idle definition: `idle_total = idle + iowait`.
- Formula:
  - `total_delta = total_current - total_previous`
  - `idle_delta = idle_current - idle_previous`
  - `busy_delta = total_delta - idle_delta`
  - `utilization_pct = clamp(busy_delta / total_delta * 100, 0, 100)`
- Granularity: per logical CPU ID.
- First sample behavior: baseline only (utilization unavailable until the second sample).
- Unavailable/collecting values are exposed as missing and rendered as `N/A` in the UI.

## SoftIRQ Charts
- SoftIRQ Distribution (bar): current rates by class
- SoftIRQ Trend (multi-line): timeseries from payload `series.softirq_classes`
- Up to 8 classes plotted in trend chart

## Network Charts
- Throughput trend: RX/TX bytes/sec over time
- Interface heatmap: one selected metric per interface
- Ranking bar: top interfaces by RX bytes/sec
- Error/drop trend: aggregated error and drop rates over time

## IRQ Source Distribution
- Aggregation of latest IRQ rows by `source_class`
- Shows rate and percent contribution

## Correlation Views
### Interface -> IRQ -> CPU Sankey
- Nodes: interfaces, IRQ nodes, top CPU nodes
- Links:
  - interface -> IRQ weighted by IRQ total rate
  - IRQ -> CPU weighted by top CPU contributions

Interpretation:
- This is co-observed relationship mapping.
- It is not causal inference.

### Network IRQ Correlation Table
- Matches IRQ rows to interfaces by `irq_row.nic`
- Splits rates by `direction` (`RX`, `TX`, `Other`)
- Shows interface packet rates alongside IRQ rates

## NUMA View
- Aggregates IRQ total rate by `irq_row.numa_node`
- Displays per-node bar chart
- Accuracy depends on NUMA labels available in collected IRQ metadata

## Topology View
Local mode source:
- `/sys/devices/system/cpu/cpu*/topology/*`
- CPU online state and node mapping

Remote mode source:
- agent telemetry `cpu_topology`
- stored snapshots in `cpu_topology_samples`

Rendered as socket -> NUMA -> core -> CPU tree with current per-CPU IRQ load annotation.

## Health Metrics
Current UI health cards use payload stats:
- IRQ load score
- SoftIRQ load score
- Network load score
- IRQ balance

IRQ balance comes from backend `irq_balance_score`:
- score from normalized entropy
- coefficient of variation
- status: `Balanced`, `Moderately Imbalanced`, or `Highly Imbalanced`

## Anomaly Detection
Backend anomaly events include:
- spike events for IRQ rate, RX bps, TX bps
- network error and drop events when non-zero

Spike logic (`detect_spikes`):
- baseline points: 8
- default multiplier: 2.0
- event generated when current >= baseline * multiplier

Frontend threshold sliders then filter displayed events:
- IRQ spike multiplier
- network error threshold
- network drop threshold
- CPU imbalance threshold

## Comparison View
`/api/visualization/compare` returns snapshots for two systems and per-metric deltas.

Displayed metrics:
- IRQ/sec
- SoftIRQ/sec
- Network RX B/s
- Network TX B/s
- Network errors
- Network drops
- IRQ balance
