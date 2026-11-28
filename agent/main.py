from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import socket
import sys
import time
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

# Started as a script, the repository root is not on the import path yet. Sent over SSH, the imports come along.
if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.collectors import (  # noqa: E402
    COMMAND_CATEGORIES, DiagnosticCommandCollector, IRQCollector, NetworkCollector, PCIeCollector, SoftIRQCollector,
    SoftnetCollector, StorageCollector, SystemCollector, collect_cpu_topology,
)

AGENT_VERSION = "1.3.0"
MAX_IRQ_ROWS = 1024
PCIE_INTERVAL_SECONDS = 10.0


def _post_json(url: str, payload: dict, token: str) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    # Bypass environment proxies for agent-to-server telemetry posts.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=10) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw) if raw else {}


def _hostname() -> str:
    return socket.gethostname() or "unknown"


def _collect_diagnostic_job(sut_id: str, job: dict) -> dict:
    payload = {"sut_id": sut_id, "lease_token": job["lease_token"], "results": [], "error": ""}
    try:
        categories = sorted(set(job.get("categories", [])) & COMMAND_CATEGORIES)
        if not categories:
            payload["error"] = "No supported diagnostic categories requested."
            return payload
        collector = DiagnosticCommandCollector(max(1.0, min(30.0, float(job.get("command_timeout_seconds", 8.0)))))
        interfaces = NetworkCollector().discover_interfaces()
        payload["results"] = [result.model_dump() for result in collector.collect(interfaces, categories)]
    except Exception as exc:
        payload["error"] = str(exc)[:2000]
    return payload


def _diagnostic_worker(server: str, sut_id: str, token: str) -> None:
    pending = None
    while True:
        try:
            if pending is None:
                response = _post_json(server.rstrip("/") + "/api/agent/diagnostics/poll", {"sut_id": sut_id}, token)
                job = response.get("job")
                if job:
                    pending = (job["job_id"], _collect_diagnostic_job(sut_id, job))
            if pending is not None:
                job_id, payload = pending
                _post_json(server.rstrip("/") + f"/api/agent/diagnostics/{job_id}/results", payload, token)
                pending = None
        except urllib.error.HTTPError as exc:
            if exc.code in {404, 409}:
                pending = None
            print(f"[irqlens-agent] diagnostics HTTP error: {exc.code}")
        except Exception as exc:
            print(f"[irqlens-agent] diagnostics retry: {exc}")
        time.sleep(2.0)


def _ip_addrs() -> List[str]:
    ips: List[str] = []
    # Prefer local interface enumeration to avoid blocking DNS lookups.
    try:
        out = subprocess.check_output(["hostname", "-I"], text=True, timeout=2)
        for token in out.split():
            ip = token.strip()
            if ip and ip not in ips:
                ips.append(ip)
        if ips:
            return ips
    except Exception:
        pass

    try:
        for item in socket.getaddrinfo(_hostname(), None):
            ip = item[4][0]
            if ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    return ips


def _read_cpu_stat_counters() -> Dict[str, tuple[int, int]]:
    counters: Dict[str, tuple[int, int]] = {}
    stat_path = Path("/proc/stat")
    if not stat_path.exists():
        return counters
    for line in stat_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.startswith("cpu") or line.startswith("cpu "):
            continue
        parts = line.split()
        cpu_label = parts[0]
        if not cpu_label.startswith("cpu") or not cpu_label[3:].isdigit():
            continue
        nums: List[int] = []
        for token in parts[1:]:
            try:
                nums.append(int(token))
            except ValueError:
                nums.append(0)
        while len(nums) < 8:
            nums.append(0)
        user, nice, system, idle, iowait, irq, softirq, steal = nums[:8]
        total = user + nice + system + idle + iowait + irq + softirq + steal
        idle_total = idle + iowait
        counters[cpu_label[3:]] = (total, idle_total)
    return counters


def _cpu_utilization_percent(
    previous: Dict[str, tuple[int, int]],
    current: Dict[str, tuple[int, int]],
) -> Dict[str, float]:
    utilization: Dict[str, float] = {}
    for cpu, (cur_total, cur_idle) in current.items():
        prev = previous.get(cpu)
        if not prev:
            continue
        prev_total, prev_idle = prev
        total_delta = cur_total - prev_total
        idle_delta = cur_idle - prev_idle
        if total_delta <= 0:
            continue
        busy = max(0, total_delta - idle_delta)
        utilization[cpu] = max(0.0, min(100.0, (busy / total_delta) * 100.0))
    return utilization


# One machine's collectors and the payloads built from them; sending them is the caller's part.
class Telemetry:
    def __init__(self, sut_id: str, telemetry_interval: float, include_virtual_disks: bool = False) -> None:
        self.sut_id = sut_id
        self.telemetry_interval = telemetry_interval
        self.irq_collector = IRQCollector()
        self.soft_collector = SoftIRQCollector()
        self.net_collector = NetworkCollector()
        self.sys_collector = SystemCollector()
        self.softnet_collector = SoftnetCollector()
        self.storage_collector = StorageCollector(include_virtual=include_virtual_disks)
        self.pcie_collector = PCIeCollector()
        self.last_sample_monotonic: Optional[float] = None
        self.pcie_collected_at = 0.0
        self.topology_cache: List[dict] = []
        self.topology_updated_at = 0.0
        self.previous_cpu_stat: Dict[str, tuple[int, int]] = _read_cpu_stat_counters()

    def registration(self, name: str, role: str, token_hint: str) -> dict:
        ts = time.time()
        system = self.sys_collector.collect(ts)
        self.topology_cache = collect_cpu_topology(str(system.get("cpu_model", "")))
        self.topology_updated_at = ts
        addresses = _ip_addrs()
        return {
            "sut_id": self.sut_id,
            "name": name,
            "address": addresses[0] if addresses else "0.0.0.0",
            "port": 8443,
            "token_hint": token_hint,
            "agent_version": AGENT_VERSION,
            "telemetry_interval": self.telemetry_interval,
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
            "interfaces": self.net_collector.discover_interfaces(),
            "ip_addresses": addresses,
            "cpu_topology": self.topology_cache,
            "role": role,
        }

    def sample(self) -> dict:
        now = time.monotonic()
        elapsed = self.telemetry_interval if self.last_sample_monotonic is None else max(1e-3, now - self.last_sample_monotonic)
        self.last_sample_monotonic = now
        ts = time.time()
        sut_id = self.sut_id

        system = self.sys_collector.collect(ts)
        system["architecture"] = platform.machine() or "unknown"

        # Refresh topology periodically to handle CPU online/offline changes.
        if (ts - self.topology_updated_at) >= 60.0 or not self.topology_cache:
            self.topology_cache = collect_cpu_topology(str(system.get("cpu_model", "")))
            self.topology_updated_at = ts

        current_cpu_stat = _read_cpu_stat_counters()
        cpu_utilization = _cpu_utilization_percent(self.previous_cpu_stat, current_cpu_stat)
        self.previous_cpu_stat = current_cpu_stat

        irq_rows = self.irq_collector.sample_rows(sut_id, ts, elapsed)[:MAX_IRQ_ROWS]
        softnet = self.softnet_collector.collect(elapsed)
        storage = self.storage_collector.collect(elapsed)
        # PCIe state changes rarely; the server keeps the last snapshot between sends.
        pcie: List[dict] = []
        if ts - self.pcie_collected_at >= PCIE_INTERVAL_SECONDS:
            pcie = self.pcie_collector.collect()
            self.pcie_collected_at = ts

        soft_totals, soft_per_cpu = self.soft_collector.parse()
        soft_rates, soft_cpu_rates = self.soft_collector.rates(soft_totals, soft_per_cpu, elapsed)

        net_rows_raw, net_global, iface_infos = self.net_collector.collect(elapsed, ts)
        net_rows = [{"sut_ip": sut_id, "sut_id": sut_id, **row} for row in net_rows_raw]

        irq_summary = {
            "total_irq_per_sec": sum(float(x.get("total_rate", 0.0)) for x in irq_rows),
            "total_softirq_per_sec": sum(float(v) for v in soft_rates.values()),
            "active_irq_lines": len(irq_rows),
            "network_related_irqs": len([x for x in irq_rows if x.get("source_class") == "network"]),
        }

        return {
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
            "cpu_topology": self.topology_cache,
            "cpu_utilization": cpu_utilization,
            "softnet": softnet if softnet["available"] else {},
            "storage": storage,
            "pcie": pcie,
        }


def run_agent(
    server: str, sut_id: str, name: str, token: str, telemetry_interval: float, heartbeat_interval: float,
    include_virtual_disks: bool = False, role: str = "",
) -> int:
    source = Telemetry(sut_id, telemetry_interval, include_virtual_disks)
    last_heartbeat = 0.0

    while True:
        try:
            reg = source.registration(name, role, "configured" if token else "none")
            _post_json(server.rstrip("/") + "/api/agent/register", reg, token)
            break
        except Exception as exc:
            print(f"[irqlens-agent] register retry: {exc}")
            time.sleep(2)

    threading.Thread(target=_diagnostic_worker, args=(server, sut_id, token), name="irqlens-diagnostics", daemon=True).start()

    while True:
        cycle_start = time.monotonic()

        try:
            payload = source.sample()
            ts = payload["timestamp"]

            _post_json(server.rstrip("/") + "/api/agent/telemetry", payload, token)

            if ts - last_heartbeat >= heartbeat_interval:
                hb = {
                    "sut_id": sut_id,
                    "agent_version": AGENT_VERSION,
                    "uptime_seconds": float(payload["system"]["uptime_seconds"]),
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


# For a server that started this process over SSH: the same payloads as JSON lines on stdout,
# diagnostic jobs as JSON lines on stdin. It ends when stdin closes, so nothing stays behind.
def run_stdio(sut_id: str, role: str = "client", telemetry_interval: float = 1.0, include_virtual_disks: bool = False) -> int:
    out = sys.stdout
    # Anything else that prints must not land inside the JSON stream.
    sys.stdout = sys.stderr
    write_lock = threading.Lock()
    stop = threading.Event()

    def send(message: dict) -> None:
        line = json.dumps(message, separators=(",", ":")) + "\n"
        try:
            with write_lock:
                out.write(line)
                out.flush()
        except (OSError, ValueError):
            # The server is gone; there is nobody left to report to.
            os._exit(0)

    def read_jobs() -> None:
        for line in sys.stdin:
            try:
                command = json.loads(line)
                if command.get("type") == "diagnostics":
                    job = command["job"]
                    send({"type": "diagnostics_result", "job_id": job["job_id"], **_collect_diagnostic_job(sut_id, job)})
            except Exception as exc:
                print(f"[irqlens-agent] command ignored: {exc}")
        stop.set()

    threading.Thread(target=read_jobs, name="irqlens-jobs", daemon=True).start()
    source = Telemetry(sut_id, telemetry_interval, include_virtual_disks)
    send({"type": "register", **source.registration(_hostname(), role, "none")})
    while not stop.is_set():
        cycle_start = time.monotonic()
        try:
            send(source.sample())
        except Exception as exc:
            print(f"[irqlens-agent] error: {exc}")
        stop.wait(max(0.05, telemetry_interval - (time.monotonic() - cycle_start)))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="IRQLENS SUT Agent")
    ap.add_argument("--server", default=os.getenv("IRQLENS_AGENT_SERVER", "http://127.0.0.1:10000"))
    ap.add_argument("--sut-id", default=os.getenv("IRQLENS_AGENT_SUT_ID", _hostname()))
    ap.add_argument("--name", default=os.getenv("IRQLENS_AGENT_NAME", _hostname()))
    ap.add_argument("--token", default=os.getenv("IRQLENS_AGENT_TOKEN", "") or os.getenv("IRQLENS_TOKEN", ""))
    ap.add_argument("--telemetry-interval", type=float, default=float(os.getenv("IRQLENS_AGENT_TELEMETRY_INTERVAL", "1.0")))
    ap.add_argument("--heartbeat-interval", type=float, default=float(os.getenv("IRQLENS_AGENT_HEARTBEAT_INTERVAL", "5.0")))
    ap.add_argument("--include-virtual-disks", action="store_true", help="Also report loop, ram, dm-*, md and zram block devices")
    ap.add_argument("--role", choices=["client", "server"], default="client", help="Side of the machine map this machine is drawn on")
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
        include_virtual_disks=args.include_virtual_disks,
        role=args.role,
    )


if __name__ == "__main__":
    raise SystemExit(main())
