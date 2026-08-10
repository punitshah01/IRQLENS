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
from .models import (
    AgentHeartbeatRequest,
    AgentRegistrationRequest,
    AgentTelemetryPayload,
    IRQSample,
    NetworkSample,
    SessionStartRequest,
    SessionStopRequest,
    SystemCreateRequest,
    SystemInfo,
    SystemRecord,
)
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


def _resolve_sut(sut_id: str | None, host: str | None) -> str:
    if sut_id:
        return sut_id
    return _resolve_host(host)


def _agent_authorized(request: Request) -> bool:
    token = settings.agent_token
    if not token:
        return True
    auth = request.headers.get("Authorization", "")
    expected = f"Bearer {token}"
    return auth.strip() == expected


def _refresh_system_statuses() -> None:
    now = time.time()
    systems = STORE.list_systems()
    for system in systems:
        age = now - float(system.last_seen)
        status = system.status
        if age > settings.stale_threshold * 3:
            status = "OFFLINE"
        elif age > settings.stale_threshold:
            status = "STALE"
        elif status in ("CONNECTING", "ERROR", "OFFLINE", "STALE"):
            status = "ONLINE"
        if status != system.status:
            STORE.upsert_system(system.model_copy(update={"status": status, "updated_at": now}))


def _register_local_system_snapshot() -> None:
    snapshot = SAMPLER.snapshot
    now = time.time()
    if snapshot:
        sys = snapshot.system
        interfaces = [i.name for i in snapshot.interfaces]
        local = SystemRecord(
            id="local",
            name="Local Host",
            hostname=sys.hostname,
            address="127.0.0.1",
            port=settings.bind_port,
            os_distribution=sys.os_distribution,
            os_version=sys.os_version,
            kernel=sys.kernel,
            architecture=sys.architecture,
            agent_version="local",
            status="ONLINE",
            last_seen=now,
            created_at=now,
            updated_at=now,
            cpu_count=sys.cpu_count,
            cpu_model=sys.cpu_model,
            memory_total_kb=sys.memory_total_kb,
            numa_nodes=sys.numa_nodes,
            interfaces=interfaces,
            ip_addresses=[],
            mode="local",
        )
    else:
        local = SystemRecord(
            id="local",
            name="Local Host",
            hostname="local",
            address="127.0.0.1",
            port=settings.bind_port,
            os_distribution="Unknown",
            os_version="Unknown",
            kernel="Unknown",
            architecture="Unknown",
            agent_version="local",
            status="CONNECTING",
            last_seen=now,
            created_at=now,
            updated_at=now,
            cpu_count=0,
            cpu_model="",
            memory_total_kb=0,
            numa_nodes=0,
            interfaces=[],
            ip_addresses=[],
            mode="local",
        )
    existing = STORE.get_system("local")
    if existing:
        local = local.model_copy(update={"created_at": existing.created_at})
    STORE.upsert_system(local)


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
    _register_local_system_snapshot()
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
def get_system(host: str | None = None, sut_id: str | None = None) -> dict:
    target = _resolve_sut(sut_id, host)
    row = STORE.latest_system(target)
    if not row:
        if SAMPLER.snapshot:
            return SAMPLER.snapshot.system.model_dump()
        raise HTTPException(status_code=404, detail="system sample not found")
    return row.model_dump()


@app.get("/api/interfaces")
def get_interfaces(host: str | None = None, sut_id: str | None = None) -> dict:
    target = _resolve_sut(sut_id, host)
    interfaces = STORE.latest_interfaces(target)
    return {"host": target, "interfaces": [item.model_dump() for item in interfaces]}


@app.get("/api/irq/current")
def irq_current(host: str | None = None, sut_id: str | None = None, limit: int = 256) -> dict:
    target = _resolve_sut(sut_id, host)
    rows = STORE.latest_irq(target, limit=limit)
    return {"host": target, "rows": [row.model_dump() for row in rows]}


@app.get("/api/irq/latest")
def irq_latest_compat(sut_ip: str, limit: int = 300) -> dict:
    rows = STORE.latest_irq(sut_ip, limit=limit)
    return {"sut_ip": sut_ip, "samples": [row.model_dump() for row in rows]}


@app.get("/api/irq/history")
def irq_history(host: str | None = None, sut_id: str | None = None, limit: int = 1000) -> dict:
    target = _resolve_sut(sut_id, host)
    rows = STORE.latest_irq(target, limit=limit)
    return {"host": target, "rows": [row.model_dump() for row in rows]}


@app.get("/api/softirq/current")
def softirq_current(host: str | None = None, sut_id: str | None = None) -> dict:
    target = _resolve_sut(sut_id, host)
    row = STORE.latest_softirq(target)
    if not row:
        return {"host": target, "sample": None}
    return {"host": target, "sample": row.model_dump()}


@app.get("/api/network/current")
def network_current(host: str | None = None, sut_id: str | None = None) -> dict:
    target = _resolve_sut(sut_id, host)
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
def network_interface(interface: str, host: str | None = None, sut_id: str | None = None) -> dict:
    target = _resolve_sut(sut_id, host)
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


@app.get("/api/systems")
def systems() -> dict:
    _register_local_system_snapshot()
    _refresh_system_statuses()
    return {"systems": [item.model_dump() for item in STORE.list_systems()]}


@app.post("/api/systems")
def add_system(req: SystemCreateRequest) -> dict:
    now = time.time()
    row = SystemRecord(
        id=req.id,
        name=req.name,
        hostname=req.name,
        address=req.address,
        port=req.port,
        os_distribution="Unknown",
        os_version="Unknown",
        kernel="Unknown",
        architecture="Unknown",
        agent_version="unknown",
        status="CONNECTING",
        last_seen=0.0,
        created_at=now,
        updated_at=now,
        mode="remote",
    )
    STORE.upsert_system(row)
    return {"ok": True, "system": row.model_dump()}


@app.get("/api/systems/{sut_id}")
def system_detail(sut_id: str) -> dict:
    row = STORE.get_system(sut_id)
    if not row:
        raise HTTPException(status_code=404, detail="system not found")
    return row.model_dump()


@app.delete("/api/systems/{sut_id}")
def delete_system(sut_id: str) -> dict:
    STORE.delete_system(sut_id)
    return {"ok": True}


@app.post("/api/systems/{sut_id}/test")
def test_system(sut_id: str) -> dict:
    row = STORE.get_system(sut_id)
    if not row:
        raise HTTPException(status_code=404, detail="system not found")
    now = time.time()
    age = now - row.last_seen
    checks = {
        "reachable": row.last_seen > 0,
        "authentication": True,
        "linux_detected": row.os_distribution.lower() != "unknown",
        "irq_collector": True,
        "network_collector": True,
        "interfaces_detected": len(row.interfaces),
        "last_seen_seconds": age if row.last_seen > 0 else None,
    }
    return {"sut_id": sut_id, "checks": checks, "status": row.status}


@app.get("/api/systems/{sut_id}/interfaces")
def system_interfaces(sut_id: str) -> dict:
    return {"sut_id": sut_id, "interfaces": [i.model_dump() for i in STORE.latest_interfaces(sut_id)]}


@app.get("/api/systems/{sut_id}/irq")
def system_irq(sut_id: str, limit: int = 256) -> dict:
    return {"sut_id": sut_id, "rows": [r.model_dump() for r in STORE.latest_irq(sut_id, limit=limit)]}


@app.get("/api/systems/{sut_id}/network")
def system_network(sut_id: str, limit: int = 400) -> dict:
    rows = STORE.latest_network(sut_id, limit=limit)
    return {"sut_id": sut_id, "rows": [r.model_dump() for r in rows]}


@app.get("/api/systems/{sut_id}/system")
def system_system(sut_id: str) -> dict:
    row = STORE.latest_system(sut_id)
    if not row:
        raise HTTPException(status_code=404, detail="system sample not found")
    return {"sut_id": sut_id, "system": row.model_dump()}


@app.get("/api/systems/{sut_id}/sessions")
def system_sessions(sut_id: str) -> dict:
    rows = [s.model_dump() for s in STORE.list_sessions() if s.sut_id == sut_id]
    return {"sut_id": sut_id, "sessions": rows}


@app.post("/api/systems/{sut_id}/sessions/start")
async def system_start_session(sut_id: str, req: SessionStartRequest) -> dict:
    req = req.model_copy(update={"sut_id": sut_id})
    return await start_session(req)


@app.post("/api/systems/{sut_id}/sessions/{session_id}/stop")
async def system_stop_session(sut_id: str, session_id: str, req: SessionStopRequest) -> dict:
    _ = sut_id
    return await stop_session(session_id, req)


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
    target = req.sut_id or "local"
    system = STORE.latest_system(target) or (SAMPLER.snapshot.system if SAMPLER.snapshot and target == "local" else None)
    hostname = system.hostname if system else "unknown"
    os_distribution = system.os_distribution if system else "Unknown"
    kernel = system.kernel if system else "Unknown"
    session = DIAGNOSTICS.start(
        categories=req.categories,
        system_hostname=hostname,
        os_distribution=os_distribution,
        kernel=kernel,
        sut_id=target,
    )
    files = DIAGNOSTICS.collect_snapshot(session.session_id, req.categories, sut_id=target)
    await WS.broadcast({"type": "session_started", "session_id": session.session_id, "timestamp": time.time()})
    return {"session": session.model_dump(), "files": [item.model_dump() for item in files]}


@app.post("/api/agent/register")
async def agent_register(payload: AgentRegistrationRequest, request: Request) -> dict:
    if not _agent_authorized(request):
        raise HTTPException(status_code=401, detail="unauthorized agent")
    now = time.time()
    existing = STORE.get_system(payload.sut_id)
    created = existing.created_at if existing else now
    row = SystemRecord(
        id=payload.sut_id,
        name=payload.name,
        hostname=payload.hostname,
        address=payload.address,
        port=payload.port,
        os_distribution=payload.os_distribution,
        os_version=payload.os_version,
        kernel=payload.kernel,
        architecture=payload.architecture,
        agent_version=payload.agent_version,
        status="ONLINE",
        last_seen=now,
        created_at=created,
        updated_at=now,
        cpu_count=payload.cpu_count,
        cpu_model=payload.cpu_model,
        memory_total_kb=payload.memory_total_kb,
        numa_nodes=payload.numa_nodes,
        interfaces=payload.interfaces,
        ip_addresses=payload.ip_addresses,
        mode="remote",
    )
    STORE.upsert_system(row)
    await WS.broadcast({"type": "system_registered", "sut_id": payload.sut_id, "timestamp": now})
    return {"ok": True, "sut_id": payload.sut_id, "heartbeat_interval": settings.heartbeat_interval, "stale_threshold": settings.stale_threshold}


@app.post("/api/agent/heartbeat")
async def agent_heartbeat(payload: AgentHeartbeatRequest, request: Request) -> dict:
    if not _agent_authorized(request):
        raise HTTPException(status_code=401, detail="unauthorized agent")
    STORE.add_heartbeat(payload.sut_id, payload.timestamp, payload.uptime_seconds, payload.agent_version)
    await WS.broadcast({"type": "agent_heartbeat", "sut_id": payload.sut_id, "timestamp": payload.timestamp})
    return {"ok": True}


@app.post("/api/agent/telemetry")
async def agent_telemetry(payload: AgentTelemetryPayload, request: Request) -> dict:
    if not _agent_authorized(request):
        raise HTTPException(status_code=401, detail="unauthorized agent")

    sut_id = payload.sut_id
    system_info = payload.system.model_copy(update={"timestamp": payload.timestamp})
    STORE.add_system(sut_id, system_info)
    STORE.add_interfaces(sut_id, payload.interfaces)

    irq_rows = []
    for row in payload.irq_rows:
        irq_rows.append(row.model_copy(update={"sut_ip": sut_id, "sut_id": sut_id}))
    if irq_rows:
        STORE.add_irq_samples(irq_rows)

    soft = payload.softirq.model_copy(update={"sut_ip": sut_id, "sut_id": sut_id, "timestamp": payload.timestamp})
    STORE.add_softirq_sample(soft)

    net_rows = []
    for row in payload.network_samples:
        net_rows.append(row.model_copy(update={"sut_ip": sut_id, "sut_id": sut_id}))
    if net_rows:
        STORE.add_network_samples(net_rows)

    existing = STORE.get_system(sut_id)
    now = time.time()
    if existing:
        updated = existing.model_copy(
            update={
                "status": "ONLINE",
                "last_seen": now,
                "updated_at": now,
                "hostname": system_info.hostname,
                "os_distribution": system_info.os_distribution,
                "os_version": system_info.os_version,
                "kernel": system_info.kernel,
                "architecture": system_info.architecture,
                "cpu_count": system_info.cpu_count,
                "cpu_model": system_info.cpu_model,
                "memory_total_kb": system_info.memory_total_kb,
                "numa_nodes": system_info.numa_nodes,
                "interfaces": [x.name for x in payload.interfaces],
            }
        )
        STORE.upsert_system(updated)

    await WS.broadcast({"type": "telemetry", "sut_id": sut_id, "timestamp": payload.timestamp})
    return {"ok": True}


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
