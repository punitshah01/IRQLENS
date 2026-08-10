from __future__ import annotations

import argparse
import json
import os
import platform
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.collectors import IRQCollector, NetworkCollector, SoftIRQCollector, SystemCollector  # noqa: E402

AGENT_VERSION = "1.0.0"


def _post_json(url: str, payload: dict, token: str) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=10) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw) if raw else {}


def _hostname() -> str:
    return socket.gethostname() or "unknown"


def _ip_addrs() -> List[str]:
    ips: List[str] = []
    try:
        for item in socket.getaddrinfo(_hostname(), None):
            ip = item[4][0]
            if ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    return ips


def _to_irq_rows(sut_id: str, ts: float, irq_collector: IRQCollector, elapsed: float) -> List[dict]:
    _, parsed = irq_collector.parse()
    rates = irq_collector.rates(parsed, elapsed)
    rows: List[dict] = []
    for irq, row in parsed.items():
        info = rates.get(irq)
        if not info:
            continue
        src_class, irq_type = irq_collector.classify(row.irq_name)
        rows.append(
            {
                "timestamp": ts,
                "sut_ip": sut_id,
                "sut_id": sut_id,
                "irq": irq,
                "irq_name": row.irq_name,
                "device": row.irq_name.split()[0] if row.irq_name else "N/A",
                "interrupt_type": irq_type,
                "affinity_list": irq_collector.affinity_for_irq(irq),
                "numa_node": irq_collector.numa_for_irq(irq),
                "nic": row.irq_name.split("-")[0] if src_class == "network" else "",
                "queue": "",
                "direction": "RX" if "rx" in row.irq_name.lower() else "TX" if "tx" in row.irq_name.lower() else "Other",
                "source_class": src_class,
                "total_count": int(info.get("total_count", 0)),
                "total_rate": float(info.get("total_rate", 0.0)),
                "cpu_rates": dict(info.get("cpu_rates", {})),
            }
        )
    rows.sort(key=lambda x: x["total_rate"], reverse=True)
    return rows[:256]


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore").strip()
    except Exception:
        return ""


def _collect_cpu_topology(cpu_model: str) -> List[dict]:
    root = Path("/sys/devices/system/cpu")
    if not root.exists():
        return []

    rows: List[dict] = []
    for cpu_dir in sorted(root.glob("cpu[0-9]*"), key=lambda p: int(p.name[3:])):
        cpu_id = int(cpu_dir.name[3:])
        topo = cpu_dir / "topology"

        socket_raw = _read_text(topo / "physical_package_id")
        core_raw = _read_text(topo / "core_id")
        thread_siblings = _read_text(topo / "thread_siblings_list")
        core_siblings = _read_text(topo / "core_siblings_list")

        numa_node = None
        for node in cpu_dir.glob("node*"):
            if node.name.startswith("node"):
                tail = node.name.replace("node", "")
                if tail.isdigit():
                    numa_node = int(tail)
                break

        online = None
        online_raw = _read_text(cpu_dir / "online")
        if online_raw in ("0", "1"):
            online = online_raw == "1"

        rows.append(
            {
                "cpu_id": cpu_id,
                "socket_id": int(socket_raw) if socket_raw.isdigit() else None,
                "core_id": int(core_raw) if core_raw.isdigit() else None,
                "numa_node": numa_node,
                "online": online,
                "thread_siblings_list": thread_siblings,
                "core_siblings_list": core_siblings,
                "cpu_model": cpu_model,
            }
        )

    return rows


def run_agent(server: str, sut_id: str, name: str, token: str, telemetry_interval: float, heartbeat_interval: float) -> int:
    irq_collector = IRQCollector()
    soft_collector = SoftIRQCollector()
    net_collector = NetworkCollector()
    sys_collector = SystemCollector()

    last_sample_monotonic = None
    last_heartbeat = 0.0
    topology_cache: List[dict] = []
    topology_updated_at = 0.0

    while True:
        try:
            ts = time.time()
            system = sys_collector.collect(ts)
            topology_cache = _collect_cpu_topology(str(system.get("cpu_model", "")))
            topology_updated_at = ts
            interfaces = net_collector.discover_interfaces()
            reg = {
                "sut_id": sut_id,
                "name": name,
                "address": _ip_addrs()[0] if _ip_addrs() else "0.0.0.0",
                "port": 8443,
                "token_hint": "configured" if token else "none",
                "agent_version": AGENT_VERSION,
                "telemetry_interval": telemetry_interval,
                "hostname": system["hostname"],
                "os_distribution": system["os_distribution"],
                "os_version": system["os_version"],
                "kernel": system["kernel"],
                "architecture": platform.machine() or "unknown",
                "cpu_count": int(system["cpu_count"]),
                "cpu_model": system["cpu_model"],
                "memory_total_kb": int(system["memory_total_kb"]),
                "numa_nodes": int(system["numa_nodes"]),
                "uptime_seconds": float(system["uptime_seconds"]),
                "interfaces": interfaces,
                "ip_addresses": _ip_addrs(),
                "cpu_topology": topology_cache,
            }
            _post_json(server.rstrip("/") + "/api/agent/register", reg, token)
            break
        except Exception:
            time.sleep(2)

    while True:
        cycle_start = time.monotonic()
        elapsed = telemetry_interval if last_sample_monotonic is None else max(1e-3, cycle_start - last_sample_monotonic)
        last_sample_monotonic = cycle_start
        ts = time.time()

        try:
            system = sys_collector.collect(ts)
            system["architecture"] = platform.machine() or "unknown"

            # Refresh topology periodically to handle CPU online/offline changes.
            if (ts - topology_updated_at) >= 60.0 or not topology_cache:
                topology_cache = _collect_cpu_topology(str(system.get("cpu_model", "")))
                topology_updated_at = ts

            irq_rows = _to_irq_rows(sut_id, ts, irq_collector, elapsed)

            soft_totals, soft_per_cpu = soft_collector.parse()
            soft_rates, soft_cpu_rates = soft_collector.rates(soft_totals, soft_per_cpu, elapsed)

            net_rows_raw, net_global, iface_infos = net_collector.collect(elapsed, ts)
            net_rows = [{"sut_ip": sut_id, "sut_id": sut_id, **row} for row in net_rows_raw]

            irq_summary = {
                "total_irq_per_sec": sum(float(x.get("total_rate", 0.0)) for x in irq_rows),
                "total_softirq_per_sec": sum(float(v) for v in soft_rates.values()),
                "active_irq_lines": len(irq_rows),
                "network_related_irqs": len([x for x in irq_rows if x.get("source_class") == "network"]),
            }

            payload = {
                "type": "telemetry",
                "sut_id": sut_id,
                "timestamp": ts,
                "system": system,
                "irq_rows": irq_rows,
                "softirq": {
                    "timestamp": ts,
                    "sut_ip": sut_id,
                    "sut_id": sut_id,
                    "totals": soft_totals,
                    "rates": soft_rates,
                    "per_cpu_rates": soft_cpu_rates,
                },
                "network_samples": net_rows,
                "interfaces": iface_infos,
                "irq_summary": irq_summary,
                "network_global": net_global,
                "cpu_topology": topology_cache,
            }

            _post_json(server.rstrip("/") + "/api/agent/telemetry", payload, token)

            if ts - last_heartbeat >= heartbeat_interval:
                hb = {
                    "sut_id": sut_id,
                    "agent_version": AGENT_VERSION,
                    "uptime_seconds": float(system["uptime_seconds"]),
                    "timestamp": ts,
                }
                _post_json(server.rstrip("/") + "/api/agent/heartbeat", hb, token)
                last_heartbeat = ts
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                print("[irqlens-agent] authentication failed")
            else:
                print(f"[irqlens-agent] HTTP error: {exc.code}")
        except Exception as exc:
            print(f"[irqlens-agent] error: {exc}")

        elapsed_loop = time.monotonic() - cycle_start
        sleep_for = max(0.05, telemetry_interval - elapsed_loop)
        time.sleep(sleep_for)


def main() -> int:
    ap = argparse.ArgumentParser(description="IRQLENS SUT Agent")
    ap.add_argument("--server", default=os.getenv("IRQLENS_AGENT_SERVER", "http://127.0.0.1:8080"))
    ap.add_argument("--sut-id", default=os.getenv("IRQLENS_AGENT_SUT_ID", _hostname()))
    ap.add_argument("--name", default=os.getenv("IRQLENS_AGENT_NAME", _hostname()))
    ap.add_argument("--token", default=os.getenv("IRQLENS_AGENT_TOKEN", ""))
    ap.add_argument("--telemetry-interval", type=float, default=float(os.getenv("IRQLENS_AGENT_TELEMETRY_INTERVAL", "1.0")))
    ap.add_argument("--heartbeat-interval", type=float, default=float(os.getenv("IRQLENS_AGENT_HEARTBEAT_INTERVAL", "5.0")))
    args = ap.parse_args()

    if os.name == "nt":
        print("IRQLENS agent is intended for Linux SUT hosts.")
        return 2

    return run_agent(
        server=args.server,
        sut_id=args.sut_id,
        name=args.name,
        token=args.token,
        telemetry_interval=max(0.1, args.telemetry_interval),
        heartbeat_interval=max(1.0, args.heartbeat_interval),
    )


if __name__ == "__main__":
    raise SystemExit(main())
