from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .models import (
    CPUTopologyEntry,
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
from .services import DiagnosticSessionService, HealthService, TelemetrySampler, detect_spikes, irq_balance_score
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


def _ip_allowed(request: Request) -> bool:
    if settings.disable_ingest_allowlist:
        return True
    if not settings.allowed_ingest_ips:
        return True
    client = request.client.host if request.client else ""
    return client in settings.allowed_ingest_ips


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


def _parse_local_cpu_topology() -> Dict[str, Any]:
    root = Path("/sys/devices/system/cpu")
    if not root.exists():
        return {"available": False, "reason": "topology files unavailable"}

    rows: List[Dict[str, Any]] = []
    cpu_model = ""
    sample = STORE.latest_system("local")
    if sample:
        cpu_model = sample.cpu_model
    for cpu_dir in sorted(root.glob("cpu[0-9]*"), key=lambda p: int(p.name[3:])):
        cpu_id = int(cpu_dir.name[3:])
        topo = cpu_dir / "topology"
        package = "0"
        core = str(cpu_id)
        if topo.exists():
            pkg_path = topo / "physical_package_id"
            core_path = topo / "core_id"
            if pkg_path.exists():
                package = pkg_path.read_text(encoding="utf-8", errors="ignore").strip() or "0"
            if core_path.exists():
                core = core_path.read_text(encoding="utf-8", errors="ignore").strip() or str(cpu_id)

        numa = "0"
        for node_path in cpu_dir.glob("node*"):
            if node_path.name.startswith("node"):
                numa = node_path.name.replace("node", "")
                break
        online = None
        online_path = cpu_dir / "online"
        if online_path.exists():
            try:
                online = online_path.read_text(encoding="utf-8", errors="ignore").strip() == "1"
            except Exception:
                online = None
        thread_siblings = ""
        core_siblings = ""
        thread_path = topo / "thread_siblings_list"
        core_sib_path = topo / "core_siblings_list"
        if thread_path.exists():
            thread_siblings = thread_path.read_text(encoding="utf-8", errors="ignore").strip()
        if core_sib_path.exists():
            core_siblings = core_sib_path.read_text(encoding="utf-8", errors="ignore").strip()
        rows.append(
            {
                "cpu_id": cpu_id,
                "socket_id": int(package) if package.isdigit() else None,
                "core_id": int(core) if core.isdigit() else None,
                "numa_node": int(numa) if numa.isdigit() else None,
                "online": online,
                "thread_siblings_list": thread_siblings,
                "core_siblings_list": core_siblings,
                "cpu_model": cpu_model,
            }
        )

    if not rows:
        return {"available": False, "reason": "no cpu topology rows"}
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

    latest_ts = STORE.latest_irq_timestamp_at(sut_id, to_ts=to_ts)
    latest_irq_rows = STORE.irq_at_timestamp(sut_id, latest_ts, limit=max(50, top_n * 6)) if latest_ts is not None else []
    latest_softirq = STORE.latest_softirq(sut_id)
    latest_network = STORE.latest_network(sut_id, limit=500)

    top_irq = sorted(latest_irq_rows, key=lambda x: float(x.total_rate), reverse=True)[: max(5, top_n)]

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
        "series": {
            "irq": irq_series,
            "network": network_series,
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
                }
                for cpu in cpu_ids
            ],
            "balance": balance,
        },
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
    rows = STORE.latest_irq(target, limit=limit)
    return {"host": target, "rows": [row.model_dump() for row in rows]}


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
    top_n: int = 20,
    from_ts: Optional[float] = None,
    to_ts: Optional[float] = None,
) -> dict:
    if not STORE.get_system(sut_id) and sut_id != "local":
        raise HTTPException(status_code=404, detail="system not found")
    return _visualization_payload(sut_id=sut_id, window_seconds=window_seconds, top_n=top_n, from_ts=from_ts, to_ts=to_ts)


@app.get("/api/systems/{sut_id}/visualization/topology")
def system_topology(sut_id: str) -> dict:
    if sut_id == "local":
        parsed = _parse_local_cpu_topology()
        if parsed.get("available"):
            rows = [CPUTopologyEntry.model_validate(row) for row in parsed.get("rows", [])]
            STORE.add_cpu_topology("local", rows, timestamp=time.time())
        return parsed
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
    if payload.cpu_topology:
        STORE.add_cpu_topology(sut_id, payload.cpu_topology, timestamp=payload.timestamp)

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
