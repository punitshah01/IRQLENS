# Contributing to IRQLENS

## Development Setup
1. Clone repository.
2. Create backend virtual environment.
3. Install dependencies.
4. Run tests.

Example:
```bash
git clone <repository-url>
cd IRQLENS
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m playwright install chromium
python -m pytest tests -q --browser chromium
```

## Coding Standards
- Keep changes focused and scoped.
- Prefer deterministic behavior for telemetry parsing and calculations.
- Preserve backward compatibility for existing API fields unless intentional change is documented.
- Keep frontend logic readable; avoid hidden side effects across render paths.

## Testing Requirements
Before opening a PR:
- Run `python -m pytest tests -q --browser chromium` with development dependencies installed.
- Tests automatically use temporary database/output directories and clear authentication defaults; security tests set their own credentials.
- Backend-only installs explicitly skip browser tests. Linux CI installs Chromium with `--with-deps`; backend CI covers Linux/Windows and Python 3.9/3.13.
- If touching telemetry/visualization behavior, add or update tests in `tests/`.
- If touching docs, verify commands and paths are still correct.

## Pull Requests
PRs should include:
- Problem statement
- Change summary
- Test evidence
- Risk/compatibility notes

If API or payload shape changes:
- Update `README.md` and `docs/AGENT_PROTOCOL.md`.
- Update impacted tests.

## Adding Collectors
Collectors live under `backend/app/collectors/`.

Guidelines:
- Keep collectors resilient to missing files/tools.
- Return safe defaults instead of raising for non-critical missing data.
- Isolate parsing logic for testability.
- Add unit tests for parser and rate logic.

## Adding Visualizations
Frontend layout is in `frontend/index.html`; rendering and chart behavior are in `frontend/app.js`. Read `docs/IRQLENS_UI_RULES.md` before UI changes.

Guidelines:
- Source chart data from existing APIs where possible.
- If new backend fields are needed, update models/tests/docs in same change.
- Document chart semantics and data sources in `docs/VISUALIZATIONS.md`.

## Modifying Agent Protocol
Protocol endpoints and payloads are defined by:
- `agent/main.py`
- `backend/app/models.py`
- `backend/app/main.py`

When changing protocol:
- Keep server backward-compatibility strategy explicit.
- Update `docs/AGENT_PROTOCOL.md` with examples.
- Add/update API tests (`tests/test_remote_agent.py`, `tests/test_api.py`).

## Licensing
The project remains unlicensed pending owner approval. Do not add a license or assume redistribution rights without an explicit owner decision. Dependencies retain their own licenses.
