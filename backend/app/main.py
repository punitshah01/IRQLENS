from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .config import settings
from .models import IngestPayload
from .store import STORE
from .ws import WS

app = FastAPI(title="IRQLENS API", version="0.1.0")
FRONTEND_INDEX = Path(__file__).resolve().parents[2] / "frontend" / "index.html"

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _ip_allowed(request: Request) -> bool:
    allowed = settings.allowed_ingest_ips
    if not allowed:
        return True
    client = request.client.host if request.client else ""
    return client in allowed


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(str(FRONTEND_INDEX), media_type="text/html")


@app.post("/api/irq/ingest")
async def ingest(payload: IngestPayload, request: Request) -> dict:
    if not _ip_allowed(request):
        raise HTTPException(status_code=403, detail="ingest client IP not allowed")
    if payload.samples:
        STORE.add_samples(payload.samples)
    if payload.host_samples:
        STORE.add_host_samples(payload.host_samples)
    await WS.broadcast({"type": "ingest", "hosts": sorted({s.sut_ip for s in payload.samples + payload.host_samples})})
    return {
        "ok": True,
        "count": len(payload.samples),
        "host_count": len(payload.host_samples),
    }


@app.get("/api/hosts")
def hosts() -> dict:
    return {"hosts": STORE.hosts()}


@app.get("/api/irq/latest")
def latest(sut_ip: str, limit: int = 300) -> dict:
    samples = STORE.latest(sut_ip=sut_ip, limit=limit)
    return {"sut_ip": sut_ip, "samples": [s.model_dump() for s in samples]}


@app.get("/api/host/latest")
def latest_host(sut_ip: str, limit: int = 120) -> dict:
    samples = STORE.latest_host(sut_ip=sut_ip, limit=limit)
    return {"sut_ip": sut_ip, "samples": [s.model_dump() for s in samples]}


@app.get("/api/summary/current")
def summary_current() -> dict:
    return {"rows": STORE.summary_current()}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await WS.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        await WS.disconnect(ws)
    except Exception:
        await WS.disconnect(ws)
