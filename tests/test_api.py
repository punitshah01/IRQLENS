from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.main import app


def test_health_endpoint():
    with TestClient(app) as client:
        r = client.get("/api/health")
        assert r.status_code == 200
        data = r.json()
        assert "application" in data
        assert "collector_status" in data


def test_sessions_lifecycle():
    with TestClient(app) as client:
        start = client.post("/api/sessions/start", json={"categories": ["system", "network"]})
        assert start.status_code == 200
        payload = start.json()
        sid = payload["session"]["session_id"]
        assert sid

        files = client.get(f"/api/sessions/{sid}/files")
        assert files.status_code == 200

        stop = client.post(f"/api/sessions/{sid}/stop", json={"reason": "manual"})
        assert stop.status_code == 200
