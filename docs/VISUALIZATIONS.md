# IRQLENS Visualizations

This document explains chart semantics, data sources, and calculations.

## Data Source Endpoints
Primary visualization payloads:
- `GET /api/systems/{sut_id}/visualization`
- `GET /api/systems/{sut_id}/visualization/topology`
- `GET /api/visualization/compare`

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

## IRQ Heatmap
- X-axis: CPU IDs
- Y-axis: top IRQ rows
- Cell value: IRQ/sec for `(irq, cpu)` pair
- Tooltip includes IRQ id/name, CPU, IRQ/sec, total count, source, device

Source fields:
- `irq_heatmap.cpus`
- `irq_heatmap.irqs`
- `irq_heatmap.values`

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
