from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Dict, List

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .config import settings
from .models import IRQSample, NetworkSample, SessionStartRequest, SessionStopRequest
from .services import DiagnosticSessionService, HealthService, TelemetrySampler
from .store import STORE
from .ws import WS


logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("irqlens.api")

app = FastAPI(title="IRQLENS API", version="0.2.0")
FRONTEND_INDEX = Path(__file__).resolve().parents[2] / "frontend" / "index.html"

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

SAMPLER = TelemetrySampler(settings=settings, store=STORE, ws_manager=WS)
DIAGNOSTICS = DiagnosticSessionService(settings=settings, store=STORE)
HEALTH = HealthService(settings=settings)


def _ip_allowed(request: Request) -> bool:
    if settings.disable_ingest_allowlist:
        return True
    if not settings.allowed_ingest_ips:
        return True
    client = request.client.host if request.client else ""
    return client in settings.allowed_ingest_ips


def _resolve_host(host: str | None) -> str:
    if host:
        return host
    hosts = STORE.hosts()
    if hosts:
        return hosts[0]
    return "local"


def _safe_path_in_output(path_str: str) -> Path:
    base = settings.output_dir.resolve()
    target = Path(path_str).resolve()
    if base == target or base in target.parents:
        return target
    raise HTTPException(status_code=403, detail="file outside output directory")


@app.on_event("startup")
async def startup() -> None:
    settings.ensure_dirs()
    await SAMPLER.start()
    logger.info("IRQLENS started interval=%s output=%s", settings.collection_interval, settings.output_dir)


@app.on_event("shutdown")
async def shutdown() -> None:
    await SAMPLER.stop()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(str(FRONTEND_INDEX), media_type="text/html")


@app.get("/api/health")
async def health() -> dict:
    ws_status = await WS.status()
    payload = HEALTH.build(
        collector_status=SAMPLER.status,
        db_ok=os.path.exists(settings.db_path),
        ws_status=ws_status,
    )
    return payload.model_dump()


@app.get("/health")
def health_legacy() -> dict:
    return {"ok": True}


@app.post("/api/irq/ingest")
async def ingest(request: Request) -> dict:
    if not _ip_allowed(request):
        raise HTTPException(status_code=403, detail="ingest client IP not allowed")

    raw = await request.json()
    raw_samples = raw.get("samples", []) if isinstance(raw, dict) else []
    raw_host = raw.get("host_samples", []) if isinstance(raw, dict) else []

    irq_samples: List[IRQSample] = []
    net_samples: List[NetworkSample] = []

    for item in raw_samples:
        try:
            irq_samples.append(IRQSample.model_validate(item))
        except Exception:
            continue

    for item in raw_host:
        if not isinstance(item, dict):
            continue
        # Legacy collector compatibility: map old host sample fields to NetworkSample.
        if "interface" not in item:
            item = {
                "timestamp": float(item.get("timestamp", time.time())),
                "sut_ip": str(item.get("sut_ip", "legacy")),
                "interface": str(item.get("nic", "all") or "all"),
                "rx_bytes": 0,
                "tx_bytes": 0,
                "rx_packets": 0,
                "tx_packets": 0,
                "rx_errors": 0,
                "tx_errors": 0,
                "rx_drops": 0,
                "tx_drops": 0,
                "rx_bps": float(item.get("rx_bps", 0.0)),
                "tx_bps": float(item.get("tx_bps", 0.0)),
                "rx_pps": float(item.get("rx_pps", 0.0)),
                "tx_pps": float(item.get("tx_pps", 0.0)),
                "rx_err_ps": 0.0,
                "tx_err_ps": 0.0,
                "rx_drop_ps": float(item.get("rx_drop_ps", 0.0)),
                "tx_drop_ps": float(item.get("tx_drop_ps", 0.0)),
            }
        try:
            net_samples.append(NetworkSample.model_validate(item))
        except Exception:
            continue

    if irq_samples:
        STORE.add_irq_samples(irq_samples)
    if net_samples:
        STORE.add_network_samples(net_samples)

    await WS.broadcast(
        {
            "type": "ingest",
            "timestamp": time.time(),
            "hosts": sorted({s.sut_ip for s in irq_samples + net_samples}),
        }
    )
    return {"ok": True, "irq_count": len(irq_samples), "network_count": len(net_samples)}


@app.get("/api/system")
def get_system(host: str | None = None) -> dict:
    target = _resolve_host(host)
    row = STORE.latest_system(target)
    if not row:
        if SAMPLER.snapshot:
            return SAMPLER.snapshot.system.model_dump()
        raise HTTPException(status_code=404, detail="system sample not found")
    return row.model_dump()


@app.get("/api/interfaces")
def get_interfaces(host: str | None = None) -> dict:
    target = _resolve_host(host)
    interfaces = STORE.latest_interfaces(target)
    return {"host": target, "interfaces": [item.model_dump() for item in interfaces]}


@app.get("/api/irq/current")
def irq_current(host: str | None = None, limit: int = 256) -> dict:
    target = _resolve_host(host)
    rows = STORE.latest_irq(target, limit=limit)
    return {"host": target, "rows": [row.model_dump() for row in rows]}


@app.get("/api/irq/latest")
def irq_latest_compat(sut_ip: str, limit: int = 300) -> dict:
    rows = STORE.latest_irq(sut_ip, limit=limit)
    return {"sut_ip": sut_ip, "samples": [row.model_dump() for row in rows]}


@app.get("/api/irq/history")
def irq_history(host: str | None = None, limit: int = 1000) -> dict:
    target = _resolve_host(host)
    rows = STORE.latest_irq(target, limit=limit)
    return {"host": target, "rows": [row.model_dump() for row in rows]}


@app.get("/api/softirq/current")
def softirq_current(host: str | None = None) -> dict:
    target = _resolve_host(host)
    row = STORE.latest_softirq(target)
    if not row:
        return {"host": target, "sample": None}
    return {"host": target, "sample": row.model_dump()}


@app.get("/api/network/current")
def network_current(host: str | None = None) -> dict:
    target = _resolve_host(host)
    rows = STORE.latest_network(target, limit=400)
    latest_by_iface: Dict[str, dict] = {}
    for row in rows:
        latest_by_iface[row.interface] = row.model_dump()
    totals = {
        "interfaces": len(latest_by_iface),
        "interfaces_up": 0,
        "interfaces_down": 0,
        "rx_bps": 0.0,
        "tx_bps": 0.0,
        "rx_pps": 0.0,
        "tx_pps": 0.0,
        "rx_err_ps": 0.0,
        "tx_err_ps": 0.0,
        "rx_drop_ps": 0.0,
        "tx_drop_ps": 0.0,
    }
    iface_state = {item.name: item.state for item in STORE.latest_interfaces(target)}
    for iface, row in latest_by_iface.items():
        state = iface_state.get(iface, "down")
        if state == "up":
            totals["interfaces_up"] += 1
        else:
            totals["interfaces_down"] += 1
        totals["rx_bps"] += float(row["rx_bps"])
        totals["tx_bps"] += float(row["tx_bps"])
        totals["rx_pps"] += float(row["rx_pps"])
        totals["tx_pps"] += float(row["tx_pps"])
        totals["rx_err_ps"] += float(row["rx_err_ps"])
        totals["tx_err_ps"] += float(row["tx_err_ps"])
        totals["rx_drop_ps"] += float(row["rx_drop_ps"])
        totals["tx_drop_ps"] += float(row["tx_drop_ps"])
    return {"host": target, "global": totals, "interfaces": list(latest_by_iface.values())}


@app.get("/api/host/latest")
def host_latest_compat(sut_ip: str, limit: int = 120) -> dict:
    rows = STORE.latest_network(sut_ip, limit=limit)
    return {"sut_ip": sut_ip, "samples": [row.model_dump() for row in rows]}


@app.get("/api/network/{interface}")
def network_interface(interface: str, host: str | None = None) -> dict:
    target = _resolve_host(host)
    rows = STORE.latest_network(target, limit=5000)
    filtered = [row.model_dump() for row in rows if row.interface == interface]
    if not filtered:
        raise HTTPException(status_code=404, detail="interface sample not found")
    return {"host": target, "interface": interface, "samples": filtered}


@app.get("/api/summary/current")
def summary_current() -> dict:
    return {"rows": STORE.summary_current()}


@app.get("/api/hosts")
def hosts() -> dict:
    return {"hosts": STORE.hosts()}


@app.get("/api/sessions")
def sessions() -> dict:
    return {"sessions": [session.model_dump() for session in STORE.list_sessions()]}


@app.get("/api/sessions/{session_id}")
def session_detail(session_id: str) -> dict:
    row = STORE.get_session(session_id)
    if not row:
        raise HTTPException(status_code=404, detail="session not found")
    return row.model_dump()


@app.post("/api/sessions/start")
async def start_session(req: SessionStartRequest) -> dict:
    system = SAMPLER.snapshot.system if SAMPLER.snapshot else None
    hostname = system.hostname if system else "unknown"
    os_distribution = system.os_distribution if system else "Unknown"
    kernel = system.kernel if system else "Unknown"
    session = DIAGNOSTICS.start(
        categories=req.categories,
        system_hostname=hostname,
        os_distribution=os_distribution,
        kernel=kernel,
    )
    files = DIAGNOSTICS.collect_snapshot(session.session_id, req.categories)
    await WS.broadcast({"type": "session_started", "session_id": session.session_id, "timestamp": time.time()})
    return {"session": session.model_dump(), "files": [item.model_dump() for item in files]}


@app.post("/api/sessions/{session_id}/stop")
async def stop_session(session_id: str, req: SessionStopRequest) -> dict:
    session = DIAGNOSTICS.stop(session_id=session_id, reason=req.reason)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    await WS.broadcast({"type": "session_stopped", "session_id": session_id, "timestamp": time.time()})
    return session.model_dump()


@app.get("/api/sessions/{session_id}/files")
def session_files(session_id: str) -> dict:
    files = DIAGNOSTICS.list_files(session_id)
    return {"session_id": session_id, "files": [item.model_dump() for item in files]}


@app.get("/api/sessions/{session_id}/download")
def session_download(session_id: str) -> FileResponse:
    archive = DIAGNOSTICS.archive_session(session_id)
    if not archive:
        raise HTTPException(status_code=404, detail="session archive not found")
    archive = _safe_path_in_output(str(archive))
    return FileResponse(str(archive), filename=archive.name, media_type="application/zip")


@app.get("/api/files")
def file_download(path: str) -> FileResponse:
    target = _safe_path_in_output(path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(str(target), filename=target.name)


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await WS.connect(ws)
    try:
        while True:
            _ = await ws.receive_text()
    except WebSocketDisconnect:
        await WS.disconnect(ws)
    except Exception:
        await WS.disconnect(ws)
