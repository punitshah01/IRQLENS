# IRQLENS UX Redesign Audit

## Goal
Transform IRQLENS from a chart-dense debug UI into a clear monitoring product that answers, in order:
1. Is my SUT healthy?
2. What is consuming CPU/IRQ resources?
3. Which CPU/NUMA node is affected?
4. Which network interface is responsible?
5. Is there an IRQ / NIC / CPU relationship?
6. When did the problem happen?
7. Can I investigate the problem?
8. Can I collect evidence?

## Current Frontend Surface
The current frontend is a single-file application in frontend/index.html with these primary views:
- Overview
- Systems
- IRQ
- SoftIRQ
- CPU
- Network
- Interfaces
- Diagnostics
- Sessions
- Logs
- Compare
- Settings

## Audit Decisions

### Global Navigation
| Element | Decision | Why |
|---|---|---|
| Sidebar brand | KEEP | Product identity is fine but visual treatment should be cleaner. |
| Overview tab | KEEP | Needed as the primary landing page. |
| Systems tab | KEEP | Required entry point for selecting remote SUTs. |
| IRQ Monitor tab | KEEP | Needed for focused IRQ investigation. |
| SoftIRQ tab | COMBINE | SoftIRQ should not be a separate top-level destination; fold into CPU/Overview context. |
| CPU tab | REBUILD | Existing presentation is technically accurate but not intuitive. |
| Network tab | REBUILD | Current controls do not create a clear per-interface investigation workflow. |
| Interfaces tab | MOVE | Interface metadata should move under Network detail, not remain separate navigation. |
| Diagnostics tab | REBUILD | Current page is a thin form without capture workflow clarity. |
| Sessions tab | REBUILD | Current table is functional but not understandable as a user workflow. |
| Logs tab | REMOVE | Debug logs are not a primary user workflow and add noise. |
| Compare tab | REMOVE | Multi-SUT comparison is not part of the primary mental model and distracts from core monitoring. |
| Settings tab | MOVE | Operational settings should remain available but outside primary monitoring flow. |

### Global Header / Controls
| Element | Decision | Why |
|---|---|---|
| Backend URL input | MOVE | Developer/debug control, not primary user control. Move to admin/settings area. |
| Host dropdown | KEEP | Required, but rename/represent as SUT selector with persistent global context. |
| Time range selector | KEEP | Required, but move into page/chart headers and out of shared crowded top bar. |
| Custom from/to inputs | KEEP | Required, but only reveal in an explicit custom range panel. |
| Top-N selector | MOVE | Advanced analysis control; belongs in IRQ page only, not global header. |
| Live/Pause toggle | KEEP | Useful, but simplify wording and position. |
| Reset Zoom | REMOVE | Current chart-first design overuses zoom; redesigned UI should reduce need for this as a global control. |
| Interval selector | MOVE | Backend/dev tuning, not a primary user control. |
| Refresh button | KEEP | Useful explicit recovery action. |
| WebSocket status badge | KEEP | Important system status signal; visually simplify. |
| System/OS/Kernel/Root badges | SIMPLIFY | Keep contextual identity, but collapse into a cleaner SUT summary header. |
| Last updated / stale indicator | KEEP | Essential to communicate data freshness. |

### Overview Page
| Element | Decision | Why |
|---|---|---|
| 12 KPI cards | SIMPLIFY | Too many KPIs at once; reduce to health-oriented summary metrics. |
| IRQ + Network Trend combined chart | REBUILD | Mixed axes and too much information reduce immediate readability. |
| CPU IRQ Heatmap | REBUILD | Useful concept, wrong presentation for overview. |
| Top IRQ Sources bar | MOVE | Better as IRQ page detail or overview findings link target. |
| Interface Heatmap | MOVE | Better in Network page; not overview-critical. |
| SoftIRQ Distribution | MOVE | Secondary analysis, not overview-critical. |
| Interface -> IRQ -> CPU Correlation Sankey | MOVE | Useful only during investigation; too heavy for overview. |
| System Health meters | COMBINE | Fold into a cleaner health/findings module. |
| Live Activity Timeline | REBUILD | Current anomaly list looks debug-oriented and depends on weak heuristics. |
| Fleet Summary table | MOVE | Belongs in Systems view, not Overview. |

### Systems Page
| Element | Decision | REBUILD | Why |
|---|---|---|
| System cards | KEEP | Card list is appropriate, but content density should be reduced. |
| System address / hostname / OS / kernel / CPU / interfaces / mode / last seen | SIMPLIFY | Keep essential system context, remove over-dense dump. |
| Open Dashboard button | REBUILD | Functionally incomplete; must select SUT, navigate, and preserve context. |

### IRQ Page
| Element | Decision | Why |
|---|---|---|
| IRQ -> CPU heatmap | KEEP | Useful for drill-down, but layout and labeling need simplification. |
| IRQ source distribution bar | KEEP | Good summary chart when clearly labeled. |
| IRQ search and sort controls | KEEP | Useful investigation controls. |
| Large IRQ monitor table | SIMPLIFY | Keep as detail view, but not the first thing a user sees. |
| Top IRQ Sources table | COMBINE | Merge with ranking/detail presentation. |
| Network IRQ correlation table | KEEP | Useful when mapping is reliable; add clear unavailable state. |

### CPU Page
| Element | Decision | Why |
|---|---|---|
| CPU Load Grid | REMOVE | Abstract grid hides topology and is less meaningful than actual hierarchy. |
| NUMA IRQ Distribution bar | KEEP | Retain as summary context, but subordinate to topology map. |
| CPU Topology text tree | REBUILD | Functionally useful but visually unacceptable for primary workflow. |
| CPU IRQ Distribution table | MOVE | Keep as detail drawer or secondary table, not primary CPU view. |

### Network Page
| Element | Decision | Why |
|---|---|---|
| Network KPI strip | SIMPLIFY | Keep fewer, more actionable KPIs. |
| Throughput trend | KEEP | Core view for network investigation. |
| Interface traffic ranking chart | REBUILD | Replace with more scannable interface list/cards. |
| Error/drop trend | KEEP | Useful when paired with selected interface context. |
| Interface statistics table | REBUILD | Must become interface-driven detail instead of dense default table. |
| Interface selector | KEEP | Functional requirement; selection must drive all network views. |

### Interfaces Page
| Element | Decision | REMOVE | Why |
|---|---|---|
| Interface cards standalone page | REMOVE | Better integrated into Network page as detail/metadata section. |

### Diagnostics Page
| Element | Decision | Why |
|---|---|---|
| Category checkbox list | KEEP | Capture scope control is necessary. |
| Start Collection button | KEEP | Core workflow action. |
| Stop Collection button | KEEP | Needed during active capture. |
| Session status text | REBUILD | Needs explicit progress/state model. |
| Session Files panel | MOVE | Should appear after completion and link into session detail. |

### Sessions Page
| Element | Decision | Why |
|---|---|---|
| Sessions table | REBUILD | Current table lacks session meaning, summary, and file visibility. |
| Files button | KEEP | Needed, but should open a proper session detail view. |
| Download link | KEEP | Important evidence workflow action. |

### Logs / Compare / Settings
| Element | Decision | Why |
|---|---|---|
| Logs page | REMOVE | Developer-only surface. |
| Compare page | REMOVE | Not part of target first-time workflow. |
| Threshold sliders | MOVE | Advanced controls belong in settings/admin. |
| Dependency/output/interval settings | MOVE | Keep out of primary monitoring flow. |

## Functional Defects Identified
1. Systems -> Open Dashboard is incomplete: it selects the host and refreshes data, but does not navigate to the dashboard view, so the action appears broken.
2. SUT context is not presented clearly at the top of the dashboard; users can lose track of whether they are viewing local or remote telemetry.
3. Network interface selection is split between heatmap click state and table dropdown state, which makes the selection model fragile and unclear.
4. Time controls are globally crowded and visually compete with chart areas.
5. CPU investigation relies on abstract or text-heavy visuals instead of an actual topology-first model.
6. Sessions are shown as raw records, not as user-created diagnostic captures with clear scope, duration, status, and files.

## Redesign Direction
### Primary navigation
- Overview
- IRQ
- Network
- CPU
- Diagnostics
- Sessions
- Systems

### Persistent global context
- Selected SUT name/ID
- Online/offline state
- Last update age
- Current time range

### Overview structure
- SUT health summary
- IRQ and network trends
- CPU/NUMA hotspot map
- Findings list

### Investigation structure
- IRQ page: top sources, IRQ table, IRQ-to-interface relationships
- Network page: interface selector, current traffic, trend, errors/drops, related IRQs, metadata
- CPU page: topology map, CPU detail, NUMA summary, hottest CPUs

### Evidence workflow structure
- Diagnostics: configure -> start -> progress -> complete
- Sessions: summary cards -> detail -> files -> download

## Implementation Guardrails
- Do not fabricate topology, history, or IRQ-to-NIC mapping.
- Prefer clear empty states over blank charts.
- Reduce simultaneous charts per screen.
- Preserve selected SUT while navigating and refreshing.
- Keep backend data semantics unchanged; fix frontend association and presentation only.
