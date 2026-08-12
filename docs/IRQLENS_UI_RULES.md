# IRQLENS UI Rules

## Product philosophy

IRQLENS is a SUT monitoring and diagnostics tool.
The default user flow is: start IRQLENS, select a SUT, monitor and investigate CPU/IRQ/Network, then capture diagnostics if needed.

## Primary navigation

- Overview
- CPU
- IRQ
- Network
- Collect Logs

## Collect Logs model

- Sessions are part of Collect Logs workflow.
- Session history is accessed from Collect Logs (not a primary navigation destination).

## Explicitly excluded from primary UI

- Admin
- Administration
- Users
- Roles
- Settings
- Management

## SUT identity

- Use actual SUT hostname when available.
- Do not display "Local Host" when a real hostname is available.
- Prefer display order: hostname, then system name, then system id.

## CPU visualization

- CPU matrix/heatmap is the primary CPU visualization.
- CPU IDs must come from actual SUT topology data.
- Do not assume array index equals CPU ID.
- Matrix dimensions must be dynamic and readability-first.
- NUMA grouping is supported where available.
- CPU topology must reflect real SUT topology data.
- CPU detail panels may exist as secondary views, but must not replace the matrix as primary visualization.

## CPU hover

- Hover should show concise per-CPU context (IRQ/SoftIRQ/utilization + topology basics when available).
- Tooltip must close when leaving the CPU cell.
- Tooltip must be hidden when navigating away from CPU pages to avoid stale overlays.
- Hover must use already loaded data (no per-hover API calls).

## Header and controls

- Keep the header minimal and SUT-focused.
- Avoid global controls that do not directly improve monitoring workflow.
- Do not add global admin/management controls to the user-facing dashboard.

## Performance principle

- Never sacrifice responsiveness for additional visualization complexity.
- Prefer incremental updates and lightweight interactions over full-page re-renders.

## Change discipline

- Make small, isolated UI changes.
- Do not perform broad frontend rewrites for narrow UI requests.
- Preserve working functionality while changing UI.
- Reuse known-good implementations from git history when restoring behavior.
