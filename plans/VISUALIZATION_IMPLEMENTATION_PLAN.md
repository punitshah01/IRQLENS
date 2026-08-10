# IRQLENS Visualization Implementation Plan

## Goal
Add a professional visualization layer on top of existing IRQLENS telemetry without removing current tables, diagnostics, exports, remote SUT behavior, or websocket behavior.

## Phase 1 Existing UI analysis
- Inventory current views, selectors, KPI cards, table render paths, and websocket update path.
- Identify reusable UI controls and layout slots for additional charts/heatmaps.

## Phase 2 Existing telemetry analysis
- Inventory existing API data and websocket payload fields for IRQ/SoftIRQ/network/system/interfaces.
- Identify missing bounded-history and topology metadata needed for trend/heatmap views.

## Phase 3 Visualization data models
- Add backend response models for visualization series, heatmaps, distribution, and health summary.
- Keep payloads bounded by time window and top-N constraints.

## Phase 4 Overview KPI redesign
- Expand overview KPI set with IRQ/sec, SoftIRQ/sec, RX/TX, Active CPUs, Active IRQs, interface count, errors, drops.
- Add explanation tooltips and semantic status indicators.

## Phase 5 IRQ trend charts
- Add real-time IRQ trend line with time-range controls (30s/1m/5m/15m/30m/1h).
- Implement pause/resume and incremental series updates.

## Phase 6 IRQ heatmaps
- Add IRQ x CPU heatmap using top-N IRQ lines and CPU rate intensity.
- Add detailed tooltip (IRQ, CPU, rate, count, source, device).

## Phase 7 CPU heatmaps
- Add CPU IRQ/SoftIRQ heatmap grid with adaptive rendering for large CPU counts.
- Support filtering and imbalance highlighting.

## Phase 8 SoftIRQ visualization
- Add SoftIRQ distribution bars and multi-line trend chart with line toggles.
- Highlight NET_RX/NET_TX dominance when present.

## Phase 9 Network visualization
- Add RX/TX trend chart, interface metric heatmap, ranking bars, and error/drop indicators.
- Add metric switchers (bytes/s, packets/s, errors/s, drops/s).

## Phase 10 IRQ/network correlation
- Add interface -> IRQ -> CPU correlation panel and fallback unavailable state.
- Add focused detail view for selected interface/IRQ/CPU path.

## Phase 11 NUMA visualization
- Add NUMA-node grouped CPU load panels when NUMA metadata is available.
- Show per-node IRQ/sec and SoftIRQ/sec totals.

## Phase 12 Anomaly detection
- Add deterministic spike/anomaly detection for IRQ, SoftIRQ, RX/TX, errors/drops, imbalance.
- Add live activity timeline from real threshold crossings.

## Phase 13 Cross-filtering
- Add global filters (system/interface/cpu/numa/irq/time-range/live pause).
- Implement click-to-filter interactions across heatmaps/charts/tables.

## Phase 14 Multi-SUT comparison
- Add dedicated comparison view for two systems with side-by-side KPI and bar deltas.
- Preserve existing single-system selector behavior.

## Phase 15 Frontend performance optimization
- Keep persistent chart instances and update datasets incrementally.
- Throttle expensive redraws and bound in-memory history windows.

## Phase 16 Testing
- Add backend tests for visualization endpoints and bounded-window behavior.
- Add calculation tests for balance score and anomaly helper logic.

## Phase 17 End-to-end validation
- Validate live behavior against real telemetry on local and remote SUTs.
- Confirm existing tables, diagnostics, exports, websocket reconnection, and offline/stale indicators still work.
