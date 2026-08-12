Before modifying IRQLENS frontend/UI behavior, read:

- docs/IRQLENS_UI_RULES.md

The UI rules are the source of truth for the intended IRQLENS user experience.

Do not reintroduce Admin/Settings/Management UI unless explicitly requested.
Do not replace the CPU matrix/heatmap with a terminal-style topology table.
Prefer small isolated changes over broad frontend rewrites.
Do not modify working architecture unnecessarily.
