from __future__ import annotations

import logging
import os
import time
import base64
import binascii
import secrets
import hashlib
import hmac
from pathlib import Path
import asyncio
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import HTTPConnection

from .collectors import collect_cpu_topology
from .config import settings
from .models import (
    CPUTopologyEntry,
    AgentHeartbeatRequest,
    AgentRegistrationRequest,
    AgentTelemetryPayload,
    AgentDiagnosticPollRequest,
    AgentDiagnosticResultsRequest,
    IRQSample,
    SessionStartRequest,
    SessionStopRequest,
    SystemCreateRequest,
    SystemInfo,
    SystemRecord,
)
from .services import DiagnosticSessionService, HealthService, TelemetrySampler, detect_spikes, irq_balance_score
from .services.exporter import sanitize
from .services.visualization import cross_numa_mismatch
from .store import STORE
from .ws import WS


logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("irqlens.api")

app = FastAPI(title="IRQLENS API", version="0.2.0")
FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
FRONTEND_INDEX = Path(__file__).resolve().parents[2] / "frontend" / "index.html"
app.mount("/frontend", StaticFiles(directory=str(FRONTEND_DIR)), name="frontend")

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
SESSION_AUTO_STOP_TASKS: Dict[str, asyncio.Task] = {}
SESSION_REPORT_TASKS: Dict[str, asyncio.Task] = {}
ALLOWED_CAPTURE_DURATIONS = {30, 60, 120, 300, 600, 900, 1800}
MEDIA_TYPES = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".json": "application/json",
    ".csv": "text/csv",
    ".zip": "application/zip",
    ".txt": "text/plain",
    ".html": "text/html",
}


def _attachment(target: Path) -> FileResponse:
    return FileResponse(
        str(target),
        filename=target.name,
        media_type=MEDIA_TYPES.get(target.suffix.lower(), "application/octet-stream"),
        headers={"X-Content-Type-Options": "nosniff"},
    )


def _num(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _cpu_to_node(sut_id: str) -> Dict[str, int]:
    return {str(item.cpu_id): item.numa_node for item in STORE.latest_cpu_topology(sut_id) if item.numa_node is not None}


def _current_irq_rows(sut_id: str, limit: int) -> List[Dict[str, Any]]:
    cpu_to_node = _cpu_to_node(sut_id)
    return [
        {**row.model_dump(), **cross_numa_mismatch(row.numa_node, row.cpu_rates, cpu_to_node)}
        for row in STORE.current_irq(sut_id, limit=limit)
    ]


def _io_payload(sut_id: str, from_ts: float, to_ts: float) -> Dict[str, Any]:
    devices = STORE.latest_json("storage_samples", sut_id, [])
    pcie = STORE.latest_json("pcie_samples", sut_id, [])
    system = STORE.latest_system(sut_id)
    read_iops = sum(_num(item.get("read_iops")) for item in devices)
    write_iops = sum(_num(item.get("write_iops")) for item in devices)
    awaits = [_num(item.get(key)) for item in devices for key in ("avg_read_await_ms", "avg_write_await_ms")]
    return {
        "sut_id": sut_id,
        "from_ts": float(from_ts),
        "to_ts": float(to_ts),
        "timestamp": time.time(),
        "irqbalance": system.irqbalance if system else "unknown",
        "storage": {
            "devices": devices,
            "series": STORE.storage_series(sut_id, from_ts, to_ts),
            "totals": {
                "read_iops": read_iops,
                "write_iops": write_iops,
                "total_iops": read_iops + write_iops,
                "read_mb_s": sum(_num(item.get("read_mb_s")) for item in devices),
                "write_mb_s": sum(_num(item.get("write_mb_s")) for item in devices),
                "max_await_ms": max(awaits, default=0.0),
            },
        },
        "pcie": {
            "devices": pcie,
            "link_warnings": sum(1 for item in pcie if item.get("link_degraded")),
            "aer_errors": int(sum(_num(item.get("aer_total")) for item in pcie)),
            "aer_increasing": sum(1 for item in pcie if item.get("aer_increasing")),
        },
        "softnet": {
            "latest": STORE.latest_json("softnet_samples", sut_id, {}),
            "series": STORE.softnet_series(sut_id, from_ts, to_ts),
        },
    }


def _resolve_host(host: Optional[str]) -> str:
    if host:
        return host
    hosts = STORE.hosts()
    if hosts:
        return hosts[0]
    return "local"


def _resolve_sut(sut_id: Optional[str], host: Optional[str]) -> str:
    if sut_id:
        return sut_id
    return _resolve_host(host)


def _agent_authorized(request: Request) -> bool:
    token = settings.agent_token or settings.api_token
    if not token:
        return True
    auth = request.headers.get("Authorization", "")
    expected = f"Bearer {token}"
    return secrets.compare_digest(auth.strip().encode(), expected.encode())


def _api_authorized(connection: HTTPConnection) -> bool:
    token = settings.api_token or settings.agent_token
    if not token:
        return True
    auth = connection.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return secrets.compare_digest(auth[7:].encode(), token.encode())
    if auth.startswith("Basic "):
        try:
            credentials = base64.b64decode(auth[6:], validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError, binascii.Error):
            return False
        username, separator, password = credentials.partition(":")
        return bool(separator and username == "irqlens" and secrets.compare_digest(password.encode(), token.encode()))
    if not auth:
        cookie = connection.cookies.get("irqlens_session", "")
        parts = cookie.split(".")
        if len(parts) == 3:
            expires, nonce, signature = parts
            if expires.isdigit() and len(expires) <= 12 and int(expires) > time.time() and len(nonce) == 32:
                expected = hmac.new(token.encode(), f"{expires}.{nonce}".encode(), hashlib.sha256).hexdigest()
                return secrets.compare_digest(signature.encode(), expected.encode())
    return False


def _origin_allowed(connection: HTTPConnection) -> bool:
    origin = connection.headers.get("Origin")
    if not origin:
        return True
    same_origin = str(connection.base_url).rstrip("/").replace("ws://", "http://").replace("wss://", "https://")
    return origin == same_origin or origin in [value for value in settings.cors_origins if value != "*"]


@app.middleware("http")
async def access_control(request: Request, call_next):
    # Decide on the routed path: request.url is rebuilt from the client-controlled Host header.
    path = request.scope["path"]
    # No route uses backslashes; on Windows they turn a static-file path into a UNC lookup.
    if "\\" in path:
        return JSONResponse(status_code=404, content={"detail": "Not Found"})
    if path == "/health":
        return await call_next(request)
    agent_request = path.startswith("/api/agent/")
    authorized = _agent_authorized(request) if agent_request else _api_authorized(request)
    if not authorized:
        return JSONResponse(
            status_code=401,
            content={"detail": "authentication required"},
            headers={"WWW-Authenticate": 'Basic realm="IRQLENS", charset="UTF-8"'},
        )
    if request.method not in {"GET", "HEAD", "OPTIONS"} and not _origin_allowed(request):
        return JSONResponse(status_code=403, content={"detail": "origin not allowed"})
    response = await call_next(request)
    token = settings.api_token or settings.agent_token
    if token and path == "/" and request.headers.get("Authorization", "").startswith("Basic "):
        session = f"{int(time.time()) + 43200}.{secrets.token_hex(16)}"
        signature = hmac.new(token.encode(), session.encode(), hashlib.sha256).hexdigest()
        response.set_cookie(
            "irqlens_session", f"{session}.{signature}", max_age=43200,
            httponly=True, secure=request.url.scheme == "https", samesite="strict",
        )
    return response


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
            name=sys.hostname or "local",
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
            name="local",
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
    local = local.model_copy(update={"sampling_interval_seconds": settings.collection_interval})
    if existing:
        local = local.model_copy(update={"created_at": existing.created_at})
    STORE.upsert_system(local)


def _safe_path_in_output(path_str: str) -> Path:
    base = settings.output_dir.resolve()
    # Another drive or UNC share is outside by definition; refuse it before resolve() contacts that host.
    drives = {Path(item).absolute().drive.lower() for item in (settings.output_dir, base)}
    if Path(path_str).absolute().drive.lower() not in drives:
        raise HTTPException(status_code=403, detail="file outside output directory")
    target = Path(path_str).resolve()
    if base == target or base in target.parents:
        return target
    raise HTTPException(status_code=403, detail="file outside output directory")


def _parse_local_cpu_topology() -> Dict[str, Any]:
    sample = STORE.latest_system("local")
    rows = collect_cpu_topology(sample.cpu_model if sample else "")
    if not rows:
        return {"available": False, "reason": "topology files unavailable"}
    return {"available": True, "rows": rows}


def _range_stats(values: List[float]) -> Dict[str, float]:
    if not values:
        return {"current": 0.0, "min": 0.0, "max": 0.0, "avg": 0.0}
    return {
        "current": float(values[-1]),
        "min": float(min(values)),
        "max": float(max(values)),
        "avg": float(sum(values) / len(values)),
    }


def _source_distribution(rows: List[IRQSample]) -> List[Dict[str, Any]]:
    bucket: Dict[str, float] = {}
    for row in rows:
        key = (row.source_class or "other").strip() or "other"
        bucket[key] = bucket.get(key, 0.0) + float(row.total_rate)
    total = sum(bucket.values()) or 1.0
    out = []
    for key, value in sorted(bucket.items(), key=lambda kv: kv[1], reverse=True):
        out.append({"source_class": key, "rate": value, "percent": (value / total) * 100.0})
    return out


def _cpu_totals(rows: List[IRQSample], softirq_per_cpu: Dict[str, float]) -> Dict[str, Dict[str, float]]:
    data: Dict[str, Dict[str, float]] = {}
    for row in rows:
        for cpu, rate in row.cpu_rates.items():
            key = str(cpu)
            if key not in data:
                data[key] = {"irq": 0.0, "softirq": 0.0, "total": 0.0}
            data[key]["irq"] += float(rate)
    for cpu, rate in softirq_per_cpu.items():
        key = str(cpu)
        if key not in data:
            data[key] = {"irq": 0.0, "softirq": 0.0, "total": 0.0}
        data[key]["softirq"] += float(rate)
    for cpu in data:
        data[cpu]["total"] = data[cpu]["irq"] + data[cpu]["softirq"]
    return data


def _visualization_payload(
    sut_id: str,
    window_seconds: int = 300,
    top_n: int = 20,
    from_ts: Optional[float] = None,
    to_ts: Optional[float] = None,
) -> Dict[str, Any]:
    now = time.time()
    if to_ts is None:
        to_ts = now
    if from_ts is None:
        from_ts = to_ts - max(30, min(3600, int(window_seconds)))
    if from_ts > to_ts:
        from_ts, to_ts = to_ts, from_ts

    irq_series = STORE.irq_rate_series(sut_id, from_ts, to_ts=to_ts)
    network_series = STORE.network_rate_series(sut_id, from_ts, to_ts=to_ts)
    softirq_series = STORE.softirq_series(sut_id, from_ts, to_ts=to_ts)
    cpu_util_series = STORE.cpu_utilization_series(sut_id, from_ts, to_ts=to_ts)

    latest_ts = STORE.latest_irq_timestamp_at(sut_id, to_ts=to_ts)
    latest_irq_rows = STORE.irq_at_timestamp(sut_id, latest_ts, limit=5000) if latest_ts is not None else []
    latest_softirq = STORE.latest_softirq(sut_id)
    latest_network = STORE.latest_network(sut_id, limit=500)
    latest_system = STORE.latest_system(sut_id)

    # Rows arrive sorted by rate; top_n <= 0 keeps every active IRQ.
    active_irq = [row for row in latest_irq_rows if float(row.total_rate) > 0.0]
    top_irq = active_irq if top_n <= 0 else active_irq[:top_n]

    cpu_ids = sorted(
        {
            str(cpu)
            for row in latest_irq_rows
            for cpu in row.cpu_rates.keys()
        }
        | set((latest_softirq.per_cpu_rates if latest_softirq else {}).keys()),
        key=lambda x: int(x),
    )
    cpu_index = {cpu: idx for idx, cpu in enumerate(cpu_ids)}
    irq_rows_for_heatmap = top_irq

    irq_heat_values: List[List[Any]] = []
    for irq_idx, row in enumerate(irq_rows_for_heatmap):
        for cpu, rate in row.cpu_rates.items():
            if str(cpu) not in cpu_index:
                continue
            irq_heat_values.append(
                [
                    irq_idx,
                    cpu_index[str(cpu)],
                    float(rate),
                    int(row.total_count),
                    row.irq,
                    row.irq_name,
                    row.device,
                    row.source_class,
                ]
            )

    soft_cpu = latest_softirq.per_cpu_rates if latest_softirq else {}
    cpu_totals = _cpu_totals(latest_irq_rows, soft_cpu)
    cpu_utilization = STORE.latest_cpu_utilization(sut_id)
    balance = irq_balance_score({cpu: data["irq"] for cpu, data in cpu_totals.items()})

    iface_map: Dict[str, Dict[str, float]] = {}
    for row in latest_network:
        iface_map[row.interface] = {
            "rx_bps": float(row.rx_bps),
            "tx_bps": float(row.tx_bps),
            "rx_pps": float(row.rx_pps),
            "tx_pps": float(row.tx_pps),
            "errors_ps": float(row.rx_err_ps + row.tx_err_ps),
            "drops_ps": float(row.rx_drop_ps + row.tx_drop_ps),
        }
    iface_rows = [{"interface": k, **v} for k, v in sorted(iface_map.items())]

    softirq_classes: Dict[str, List[List[float]]] = {}
    for row in softirq_series:
        ts = float(row["timestamp"])
        for key, val in row["rates"].items():
            softirq_classes.setdefault(key, []).append([ts, float(val)])

    irq_values = [float(row["irq_rate"]) for row in irq_series]
    rx_values = [float(row["rx_bps"]) for row in network_series]
    tx_values = [float(row["tx_bps"]) for row in network_series]
    soft_values = [float(sum(row["rates"].values())) for row in softirq_series]
    err_values = [float(row["rx_err_ps"] + row["tx_err_ps"]) for row in network_series]
    drop_values = [float(row["rx_drop_ps"] + row["tx_drop_ps"]) for row in network_series]

    anomaly_events: List[Dict[str, Any]] = []
    anomaly_events.extend(
        [dict(x, metric="irq_rate") for x in detect_spikes([(r["timestamp"], r["irq_rate"]) for r in irq_series], multiplier=2.0)]
    )
    anomaly_events.extend(
        [dict(x, metric="net_rx_bps") for x in detect_spikes([(r["timestamp"], r["rx_bps"]) for r in network_series], multiplier=2.0)]
    )
    anomaly_events.extend(
        [dict(x, metric="net_tx_bps") for x in detect_spikes([(r["timestamp"], r["tx_bps"]) for r in network_series], multiplier=2.0)]
    )
    for row in network_series:
        total_err = float(row["rx_err_ps"] + row["tx_err_ps"])
        total_drop = float(row["rx_drop_ps"] + row["tx_drop_ps"])
        if total_err > 0.0:
            anomaly_events.append({"type": "error", "metric": "network_errors", "timestamp": row["timestamp"], "current": total_err})
        if total_drop > 0.0:
            anomaly_events.append({"type": "drop", "metric": "network_drops", "timestamp": row["timestamp"], "current": total_drop})

    anomaly_events.sort(key=lambda x: float(x.get("timestamp", 0.0)), reverse=True)
    anomaly_events = anomaly_events[:100]

    source_dist = _source_distribution(latest_irq_rows)
    health = {
        "irq_load_score": min(100.0, _range_stats(irq_values)["current"]),
        "softirq_load_score": min(100.0, _range_stats(soft_values)["current"]),
        "network_load_score": min(100.0, (_range_stats(rx_values)["current"] + _range_stats(tx_values)["current"]) / 2.0),
        "irq_balance": balance,
    }

    return {
        "sut_id": sut_id,
        "window_seconds": int(max(1, to_ts - from_ts)),
        "from_ts": float(from_ts),
        "to_ts": float(to_ts),
        "timestamp": now,
        "irqbalance": latest_system.irqbalance if latest_system else "unknown",
        "series": {
            "irq": irq_series,
            "network": network_series,
            "cpu_utilization": cpu_util_series,
            "softirq_total": [
                {"timestamp": row["timestamp"], "value": float(sum(row["rates"].values()))}
                for row in softirq_series
            ],
            "softirq_classes": softirq_classes,
        },
        "stats": {
            "irq": _range_stats(irq_values),
            "softirq": _range_stats(soft_values),
            "network_rx": _range_stats(rx_values),
            "network_tx": _range_stats(tx_values),
            "network_errors": _range_stats(err_values),
            "network_drops": _range_stats(drop_values),
        },
        "top_irq_sources": [
            {
                "irq": row.irq,
                "name": row.irq_name,
                "source_class": row.source_class,
                "device": row.device,
                "rate": row.total_rate,
                "total_count": row.total_count,
            }
            for row in top_irq
        ],
        "irq_distribution": source_dist,
        "irq_heatmap": {
            "irqs": [
                {
                    "irq": row.irq,
                    "name": row.irq_name,
                    "device": row.device,
                    "source_class": row.source_class,
                    "total_rate": row.total_rate,
                    "total_count": row.total_count,
                }
                for row in irq_rows_for_heatmap
            ],
            "cpus": cpu_ids,
            "values": irq_heat_values,
        },
        "cpu_heatmap": {
            "cpus": cpu_ids,
            "values": [
                {
                    "cpu": cpu,
                    "irq_rate": cpu_totals.get(cpu, {}).get("irq", 0.0),
                    "softirq_rate": cpu_totals.get(cpu, {}).get("softirq", 0.0),
                    "total_rate": cpu_totals.get(cpu, {}).get("total", 0.0),
                    "cpu_utilization": cpu_utilization.get(cpu, 0.0),
                }
                for cpu in cpu_ids
            ],
            "balance": balance,
        },
        "cpu_utilization": cpu_utilization,
        "network_interfaces": {
            "rows": iface_rows,
            "ranking_rx": sorted(iface_rows, key=lambda x: x["rx_bps"], reverse=True),
            "ranking_tx": sorted(iface_rows, key=lambda x: x["tx_bps"], reverse=True),
        },
        "anomalies": anomaly_events,
        "health": health,
    }


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


@app.get("/api/system")
def get_system(host: Optional[str] = None, sut_id: Optional[str] = None) -> dict:
    target = _resolve_sut(sut_id, host)
    row = STORE.latest_system(target)
    if not row:
        if SAMPLER.snapshot:
            return SAMPLER.snapshot.system.model_dump()
        raise HTTPException(status_code=404, detail="system sample not found")
    return row.model_dump()


@app.get("/api/interfaces")
def get_interfaces(host: Optional[str] = None, sut_id: Optional[str] = None) -> dict:
    target = _resolve_sut(sut_id, host)
    interfaces = STORE.latest_interfaces(target)
    return {"host": target, "interfaces": [item.model_dump() for item in interfaces]}


@app.get("/api/irq/current")
def irq_current(host: Optional[str] = None, sut_id: Optional[str] = None, limit: int = 256) -> dict:
    target = _resolve_sut(sut_id, host)
    return {"host": target, "rows": _current_irq_rows(target, limit)}


@app.get("/api/irq/latest")
def irq_latest_compat(sut_ip: str, limit: int = 300) -> dict:
    rows = STORE.latest_irq(sut_ip, limit=limit)
    return {"sut_ip": sut_ip, "samples": [row.model_dump() for row in rows]}


@app.get("/api/irq/history")
def irq_history(host: Optional[str] = None, sut_id: Optional[str] = None, limit: int = 1000) -> dict:
    target = _resolve_sut(sut_id, host)
    rows = STORE.latest_irq(target, limit=limit)
    return {"host": target, "rows": [row.model_dump() for row in rows]}


@app.get("/api/softirq/current")
def softirq_current(host: Optional[str] = None, sut_id: Optional[str] = None) -> dict:
    target = _resolve_sut(sut_id, host)
    row = STORE.latest_softirq(target)
    if not row:
        return {"host": target, "sample": None}
    return {"host": target, "sample": row.model_dump()}


@app.get("/api/network/current")
def network_current(host: Optional[str] = None, sut_id: Optional[str] = None) -> dict:
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
def network_interface(interface: str, host: Optional[str] = None, sut_id: Optional[str] = None) -> dict:
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


@app.get("/api/systems/{sut_id}/visualization")
def system_visualization(
    sut_id: str,
    window_seconds: int = 300,
    top_n: int = 25,
    from_ts: Optional[float] = None,
    to_ts: Optional[float] = None,
) -> dict:
    if not STORE.get_system(sut_id) and sut_id != "local":
        raise HTTPException(status_code=404, detail="system not found")
    return _visualization_payload(sut_id=sut_id, window_seconds=window_seconds, top_n=top_n, from_ts=from_ts, to_ts=to_ts)


@app.get("/api/systems/{sut_id}/io")
def system_io(
    sut_id: str,
    window_seconds: int = 300,
    from_ts: Optional[float] = None,
    to_ts: Optional[float] = None,
) -> dict:
    if not STORE.get_system(sut_id) and sut_id != "local":
        raise HTTPException(status_code=404, detail="system not found")
    end = time.time() if to_ts is None else to_ts
    start = end - max(30, min(3600, int(window_seconds))) if from_ts is None else from_ts
    return _io_payload(sut_id, min(start, end), max(start, end))


@app.get("/api/systems/{sut_id}/visualization/topology")
def system_topology(sut_id: str) -> dict:
    if sut_id == "local":
        # The sampler keeps local topology current; parse directly only before its first tick.
        stored = STORE.latest_cpu_topology("local")
        if stored:
            return {"available": True, "rows": [row.model_dump() for row in stored]}
        return _parse_local_cpu_topology()
    system = STORE.get_system(sut_id)
    if not system:
        raise HTTPException(status_code=404, detail="system not found")
    topo = STORE.latest_cpu_topology(sut_id)
    if topo:
        return {"available": True, "sut_id": sut_id, "rows": [row.model_dump() for row in topo]}
    return {
        "available": False,
        "reason": "topology metadata not provided by remote agent yet",
        "sut_id": sut_id,
        "numa_nodes": system.numa_nodes,
        "cpu_count": system.cpu_count,
    }


@app.get("/api/visualization/compare")
def visualization_compare(a: str, b: str, window_seconds: int = 300) -> dict:
    if not a or not b:
        raise HTTPException(status_code=400, detail="both systems must be specified")
    first = _visualization_payload(a, window_seconds=window_seconds, top_n=10)
    second = _visualization_payload(b, window_seconds=window_seconds, top_n=10)

    def _snapshot(payload: dict) -> dict:
        return {
            "irq_per_sec": payload["stats"]["irq"]["current"],
            "softirq_per_sec": payload["stats"]["softirq"]["current"],
            "network_rx_bps": payload["stats"]["network_rx"]["current"],
            "network_tx_bps": payload["stats"]["network_tx"]["current"],
            "network_errors": payload["stats"]["network_errors"]["current"],
            "network_drops": payload["stats"]["network_drops"]["current"],
            "irq_balance": payload["cpu_heatmap"]["balance"]["score"],
        }

    a_now = _snapshot(first)
    b_now = _snapshot(second)

    deltas: Dict[str, float] = {}
    for key in a_now.keys():
        deltas[key] = float(a_now[key]) - float(b_now[key])

    return {
        "window_seconds": int(window_seconds),
        "systems": {
            a: a_now,
            b: b_now,
        },
        "deltas": deltas,
    }


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
    return {"sut_id": sut_id, "rows": _current_irq_rows(sut_id, limit)}


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
    return {
        **row.model_dump(),
        "remote_diagnostics": STORE.diagnostic_jobs(session_id),
        "capture_quality": DIAGNOSTICS.capture_quality(session_id),
    }


@app.post("/api/sessions/{session_id}/quality")
async def check_capture_quality(session_id: str) -> dict:
    session = STORE.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    if session.status == "running":
        raise HTTPException(status_code=409, detail="capture is still running")
    try:
        quality = await asyncio.to_thread(DIAGNOSTICS.refresh_capture_quality, session_id)
    except ValueError:
        raise HTTPException(status_code=409, detail="capture files are outside the configured output directory")
    if quality is None:
        raise HTTPException(status_code=404, detail="session not found")
    return quality


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str) -> dict:
    auto_task = SESSION_AUTO_STOP_TASKS.pop(session_id, None)
    if auto_task:
        auto_task.cancel()
    report_task = SESSION_REPORT_TASKS.pop(session_id, None)
    if report_task:
        report_task.cancel()
    deleted = DIAGNOSTICS.delete_session(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="session not found")
    await WS.broadcast({"type": "session_deleted", "session_id": session_id, "timestamp": time.time()})
    return {"ok": True, "session_id": session_id}


@app.post("/api/sessions/{session_id}/report")
async def generate_session_report(session_id: str) -> dict:
    session = STORE.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    if session.status == "running":
        raise HTTPException(status_code=409, detail="cannot generate report for a running session")

    existing_task = SESSION_REPORT_TASKS.get(session_id)
    if existing_task and not existing_task.done():
        return {"session_id": session_id, "status": "pending"}

    STORE.update_session_report(session_id, report_status="pending", report_path="", report_error="")

    async def _run_report() -> None:
        try:
            report_path = await asyncio.to_thread(DIAGNOSTICS.generate_html_report, session_id)
            if not report_path:
                STORE.update_session_report(session_id, report_status="failed", report_path="", report_error="session not found")
                return
            await WS.broadcast(
                {
                    "type": "session_report_ready",
                    "session_id": session_id,
                    "timestamp": time.time(),
                    "report_path": str(report_path),
                }
            )
        except Exception as exc:
            logger.exception("session report generation failed for %s", session_id)
            _ = exc
            STORE.update_session_report(
                session_id,
                report_status="failed",
                report_path="",
                report_error="Report generation failed. Check the session report logs.",
            )
        finally:
            SESSION_REPORT_TASKS.pop(session_id, None)

    task = asyncio.create_task(_run_report(), name=f"irqlens-session-report-{session_id}")
    SESSION_REPORT_TASKS[session_id] = task
    return {"session_id": session_id, "status": "pending"}


@app.get("/api/sessions/{session_id}/report")
def session_report_status(session_id: str) -> dict:
    session = STORE.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    report_path = session.report_path or ""
    has_report = bool(report_path and Path(report_path).exists())
    status = session.report_status
    if status == "ready" and not has_report:
        status = "none"
    return {
        "session_id": session_id,
        "status": status,
        "report_path": report_path if has_report else "",
        "report_url": f"/api/sessions/{quote(session_id, safe='')}/report/view" if has_report else "",
        "error": session.report_error or "",
    }


@app.get("/api/sessions/{session_id}/report/view")
def session_report_view(session_id: str) -> FileResponse:
    session = STORE.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    if session.report_status != "ready" or not session.report_path:
        raise HTTPException(status_code=409, detail="report is not ready")
    target = _safe_path_in_output(session.report_path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="report file not found")
    return FileResponse(
        str(target), media_type="text/html", filename=target.name,
        content_disposition_type="inline",
        headers={
            "Content-Security-Policy": "sandbox allow-same-origin allow-downloads; default-src 'none'; style-src 'unsafe-inline'; img-src data:",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get("/api/sessions/{session_id}/report/{artifact_path:path}")
def session_report_artifact(session_id: str, artifact_path: str) -> FileResponse:
    session = STORE.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    base = _safe_path_in_output(session.output_dir)
    target = _safe_path_in_output(str(base / artifact_path))
    if base not in target.parents:
        raise HTTPException(status_code=403, detail="file outside session directory")
    if not target.is_file() or not any(Path(item.path).resolve() == target for item in STORE.session_files(session_id)):
        raise HTTPException(status_code=404, detail="session file not found")
    return _attachment(target)


@app.get("/api/sessions/{session_id}/workbook")
async def session_workbook(session_id: str) -> FileResponse:
    session = STORE.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    if session.status == "running":
        raise HTTPException(status_code=409, detail="capture is still running")
    target = _safe_path_in_output(str(DIAGNOSTICS.workbook_path(session)))
    if not target.is_file() and not await asyncio.to_thread(DIAGNOSTICS.write_workbook, session_id):
        raise HTTPException(status_code=404, detail="session files not found")
    return _attachment(target)


@app.post("/api/sessions")
@app.post("/api/sessions/start")
async def start_session(req: SessionStartRequest) -> dict:
    if int(req.duration_seconds) not in ALLOWED_CAPTURE_DURATIONS:
        raise HTTPException(status_code=400, detail="duration_seconds must be one of: 30, 60, 120, 300, 600, 900, 1800")
    target = req.sut_id or "local"
    system = STORE.latest_system(target) or (SAMPLER.snapshot.system if SAMPLER.snapshot and target == "local" else None)
    hostname = system.hostname if system else "unknown"
    os_distribution = system.os_distribution if system else "Unknown"
    kernel = system.kernel if system else "Unknown"
    try:
        session = DIAGNOSTICS.start(
            session_name=req.session_name,
            categories=req.categories,
            system_hostname=hostname,
            os_distribution=os_distribution,
            kernel=kernel,
            sut_id=target,
            duration_seconds=req.duration_seconds,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    files = DIAGNOSTICS.collect_snapshot(session.session_id, req.categories, sut_id=target)

    async def _auto_stop_session(session_id: str, duration_seconds: int) -> None:
        try:
            await asyncio.sleep(max(1, int(duration_seconds)))
            existing = STORE.get_session(session_id)
            if not existing or existing.status != "running":
                return
            DIAGNOSTICS.collect_snapshot(session_id, existing.categories, sut_id=existing.sut_id or "local")
            stopped = DIAGNOSTICS.stop(session_id=session_id, reason="duration-complete")
            if stopped:
                await WS.broadcast({"type": "session_stopped", "session_id": session_id, "timestamp": time.time()})
        finally:
            SESSION_AUTO_STOP_TASKS.pop(session_id, None)

    task = asyncio.create_task(_auto_stop_session(session.session_id, req.duration_seconds), name=f"irqlens-session-{session.session_id}")
    SESSION_AUTO_STOP_TASKS[session.session_id] = task
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
        sampling_interval_seconds=payload.telemetry_interval,
    )
    STORE.upsert_system(row)
    if payload.cpu_topology:
        STORE.add_cpu_topology(payload.sut_id, payload.cpu_topology, timestamp=now)
    await WS.broadcast({"type": "system_registered", "sut_id": payload.sut_id, "timestamp": now})
    return {"ok": True, "sut_id": payload.sut_id, "heartbeat_interval": settings.heartbeat_interval, "stale_threshold": settings.stale_threshold}


@app.post("/api/agent/heartbeat")
async def agent_heartbeat(payload: AgentHeartbeatRequest, request: Request) -> dict:
    if not _agent_authorized(request):
        raise HTTPException(status_code=401, detail="unauthorized agent")
    STORE.add_heartbeat(payload.sut_id, payload.timestamp, payload.uptime_seconds, payload.agent_version)
    await WS.broadcast({"type": "agent_heartbeat", "sut_id": payload.sut_id, "timestamp": payload.timestamp})
    return {"ok": True}


@app.post("/api/agent/diagnostics/poll")
def agent_diagnostics_poll(payload: AgentDiagnosticPollRequest) -> dict:
    if not STORE.get_system(payload.sut_id):
        raise HTTPException(status_code=404, detail="system not registered")
    job = STORE.claim_diagnostics(payload.sut_id)
    if job:
        job["command_timeout_seconds"] = min(30.0, settings.command_timeout_seconds)
    return {"job": job}


@app.post("/api/agent/diagnostics/{job_id}/results")
async def agent_diagnostics_results(job_id: str, payload: AgentDiagnosticResultsRequest) -> dict:
    job = STORE.diagnostic_job(job_id)
    if not job or job["sut_id"] != payload.sut_id:
        raise HTTPException(status_code=404, detail="diagnostic job not found")
    if not secrets.compare_digest(job["lease_token"], payload.lease_token):
        raise HTTPException(status_code=409, detail="diagnostic job lease expired")
    if job["status"] in {"completed", "failed"}:
        return {"ok": True}
    if job["status"] != "running":
        raise HTTPException(status_code=409, detail="diagnostic job not claimed")
    error = payload.error
    if not payload.results and not error:
        error = "Agent returned no diagnostic results."
    if any(not result.success for result in payload.results) and not error:
        error = "One or more commands failed or were unavailable; see command output."
    try:
        await asyncio.to_thread(DIAGNOSTICS.record_remote_diagnostics, job, payload.results, error)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await WS.broadcast({"type": "session_diagnostics_ready", "session_id": job["session_id"], "timestamp": time.time()})
    return {"ok": True}


@app.post("/api/agent/telemetry")
async def agent_telemetry(payload: AgentTelemetryPayload, request: Request) -> dict:
    if not _agent_authorized(request):
        raise HTTPException(status_code=401, detail="unauthorized agent")

    sut_id = payload.sut_id
    system_info = payload.system.model_copy(update={"timestamp": payload.timestamp})
    irq_rows = [row.model_copy(update={"sut_ip": sut_id, "sut_id": sut_id}) for row in payload.irq_rows]
    soft = payload.softirq.model_copy(update={"sut_ip": sut_id, "sut_id": sut_id, "timestamp": payload.timestamp})
    net_rows = [row.model_copy(update={"sut_ip": sut_id, "sut_id": sut_id}) for row in payload.network_samples]

    with STORE.transaction():
        STORE.add_system(sut_id, system_info)
        STORE.add_interfaces(sut_id, payload.interfaces)
        STORE.add_irq_samples(irq_rows)
        STORE.add_softirq_sample(soft)
        STORE.add_network_samples(net_rows)
        if payload.cpu_topology:
            STORE.add_cpu_topology(sut_id, payload.cpu_topology, timestamp=payload.timestamp)
        if payload.cpu_utilization:
            STORE.add_cpu_utilization(sut_id, payload.cpu_utilization, timestamp=payload.timestamp)
        if payload.softnet:
            STORE.add_softnet(sut_id, sanitize(payload.softnet), payload.timestamp)
        if payload.storage:
            STORE.add_storage(sut_id, sanitize(payload.storage), payload.timestamp)
        if payload.pcie:
            STORE.add_pcie(sut_id, sanitize(payload.pcie), payload.timestamp)
        STORE.prune(sut_id, payload.timestamp)

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

    await WS.broadcast(
        {
            "type": "telemetry",
            "sut_id": sut_id,
            "timestamp": payload.timestamp,
            "cpu_utilization": payload.cpu_utilization,
        }
    )
    return {"ok": True}


@app.post("/api/sessions/{session_id}/stop")
async def stop_session(session_id: str, req: SessionStopRequest) -> dict:
    auto_task = SESSION_AUTO_STOP_TASKS.pop(session_id, None)
    if auto_task:
        auto_task.cancel()
    existing = STORE.get_session(session_id)
    if existing and existing.status == "running":
        DIAGNOSTICS.collect_snapshot(session_id, existing.categories, sut_id=existing.sut_id or "local")
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
    return _attachment(archive)


@app.get("/api/files")
def file_download(path: str) -> FileResponse:
    target = _safe_path_in_output(path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    return _attachment(target)


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    if not _api_authorized(ws) or not _origin_allowed(ws):
        await ws.close(code=1008)
        return
    await WS.connect(ws)
    try:
        while True:
            _ = await ws.receive_text()
    except WebSocketDisconnect:
        await WS.disconnect(ws)
    except Exception:
        await WS.disconnect(ws)
