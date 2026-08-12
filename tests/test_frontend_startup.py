from pathlib import Path


def test_frontend_defaults_to_overview_without_persisted_view_restore():
    app_js = Path(__file__).resolve().parents[1] / "frontend" / "app.js"
    text = app_js.read_text(encoding="utf-8")

    assert 'view: initialViewFromUrl()' in text
    assert 'storage.get("irqlens:view"' not in text
