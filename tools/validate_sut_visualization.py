from __future__ import annotations

import argparse
import json
import platform
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


@dataclass
class CheckResult:
    status: str
    message: str


def _read_text(path: str) -> str:
    return Path(path).read_text(encoding="utf-8", errors="ignore")


def _safe_read(path: str) -> Optional[str]:
    try:
        return _read_text(path)
    except Exception:
        return None


def _parse_interrupts(text: str) -> Tuple[int, int, Dict[str, int], int]:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return 0, 0, {}, 0
    header = lines[0].split()
    cpu_cols = [h for h in header if h.upper().startswith("CPU")]
    cpu_count = len(cpu_cols)
    per_irq: Dict[str, int] = {}
    total = 0
    irq_lines = 0
    for ln in lines[1:]:
        parts = ln.split()
        if len(parts) < 2:
            continue
        irq = parts[0].rstrip(":")
        counts: List[int] = []
        for p in parts[1 : 1 + cpu_count]:
            try:
                counts.append(int(p))
            except ValueError:
                counts.append(0)
        if not counts:
            continue
        irq_total = sum(counts)
        per_irq[irq] = irq_total
        total += irq_total
        irq_lines += 1
    return total, cpu_count, per_irq, irq_lines


def _parse_softirqs(text: str) -> Tuple[int, Dict[str, int]]:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    out: Dict[str, int] = {}
    total = 0
    for ln in lines[1:]:
        if ":" not in ln:
            continue
        name, rest = ln.split(":", 1)
        vals = []
        for x in rest.split():
            try:
                vals.append(int(x))
            except ValueError:
                vals.append(0)
        s = sum(vals)
        out[name.strip()] = s
        total += s
    return total, out


def _parse_netdev(text: str) -> Tuple[Dict[str, Dict[str, int]], int, int]:
    rows: Dict[str, Dict[str, int]] = {}
    rx_total = 0
    tx_total = 0
    lines = text.splitlines()[2:]
    for ln in lines:
        if ":" not in ln:
            continue
        iface, payload = ln.split(":", 1)
        iface = iface.strip()
        cols = payload.split()
        if len(cols) < 16:
            continue
        rx_bytes = int(cols[0])
        tx_bytes = int(cols[8])
        rx_total += rx_bytes
        tx_total += tx_bytes
        rows[iface] = {
            "rx_bytes": rx_bytes,
            "tx_bytes": tx_bytes,
            "rx_packets": int(cols[1]),
            "tx_packets": int(cols[9]),
            "rx_err": int(cols[2]),
            "tx_err": int(cols[10]),
            "rx_drop": int(cols[3]),
            "tx_drop": int(cols[11]),
        }
    return rows, rx_total, tx_total


def _list_sys_ifaces() -> List[str]:
    root = Path("/sys/class/net")
    if not root.exists():
        return []
    return sorted([p.name for p in root.iterdir() if p.is_dir() or p.is_symlink()])


def _collect_topology() -> List[Dict[str, object]]:
    root = Path("/sys/devices/system/cpu")
    if not root.exists():
        return []
    rows: List[Dict[str, object]] = []
    for cpu_dir in sorted(root.glob("cpu[0-9]*"), key=lambda p: int(p.name[3:])):
        cpu_id = int(cpu_dir.name[3:])
        topo = cpu_dir / "topology"

        def _txt(path: Path) -> str:
            try:
                return path.read_text(encoding="utf-8", errors="ignore").strip()
            except Exception:
                return ""

        socket = _txt(topo / "physical_package_id")
        core = _txt(topo / "core_id")
        tsib = _txt(topo / "thread_siblings_list")
        csib = _txt(topo / "core_siblings_list")
        online_raw = _txt(cpu_dir / "online")
        online = None
        if online_raw in ("0", "1"):
            online = online_raw == "1"

        numa = None
        for n in cpu_dir.glob("node*"):
            if n.name.startswith("node"):
                tail = n.name.replace("node", "")
                if tail.isdigit():
                    numa = int(tail)
                break

        rows.append(
            {
                "cpu_id": cpu_id,
                "socket_id": int(socket) if socket.isdigit() else None,
                "core_id": int(core) if core.isdigit() else None,
                "thread_siblings_list": tsib,
                "core_siblings_list": csib,
                "numa_node": numa,
                "online": online,
            }
        )
    return rows


def _numa_nodes() -> List[str]:
    root = Path("/sys/devices/system/node")
    if not root.exists():
        return []
    return sorted([p.name for p in root.iterdir() if p.name.startswith("node")])


def _http_get_json(base: str, path: str) -> Optional[dict]:
    url = base.rstrip("/") + path
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            payload = r.read().decode("utf-8")
        return json.loads(payload)
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError):
        return None


def _status_line(status: str, msg: str) -> str:
    return f"{status} {msg}"


def run_validation(
    server_url: str,
    sut_id: str,
    interval: float,
    mode: str,
) -> Tuple[List[CheckResult], List[CheckResult], List[CheckResult]]:
    passes: List[CheckResult] = []
    warns: List[CheckResult] = []
    fails: List[CheckResult] = []

    os_name = platform.system().lower()
    auto_local = os_name == "linux"
    if mode == "local":
        run_local_checks = True
    elif mode == "api-only":
        run_local_checks = False
    else:
        run_local_checks = auto_local

    if run_local_checks:
        required_paths = [
            "/proc/interrupts",
            "/proc/softirqs",
            "/proc/net/dev",
            "/proc/net/snmp",
            "/proc/net/netstat",
            "/proc/net/softnet_stat",
        ]
        for path in required_paths:
            text = _safe_read(path)
            if text is None:
                fails.append(CheckResult("FAIL", f"{path} not readable"))
            else:
                passes.append(CheckResult("PASS", f"{path} readable"))

        sys_ifaces = _list_sys_ifaces()
        if sys_ifaces:
            passes.append(CheckResult("PASS", f"{len(sys_ifaces)} interfaces detected in /sys/class/net"))
        else:
            fails.append(CheckResult("FAIL", "No interfaces found in /sys/class/net"))

        intr1 = _safe_read("/proc/interrupts")
        soft1 = _safe_read("/proc/softirqs")
        net1 = _safe_read("/proc/net/dev")
        if intr1 is None or soft1 is None or net1 is None:
            fails.append(CheckResult("FAIL", "Cannot sample dynamic counters due to unreadable proc files"))
        else:
            intr_total1, cpu_count, per_irq1, irq_lines = _parse_interrupts(intr1)
            soft_total1, _ = _parse_softirqs(soft1)
            net_rows1, rx_total1, tx_total1 = _parse_netdev(net1)

            passes.append(CheckResult("PASS", f"{cpu_count} CPUs detected from /proc/interrupts header"))
            passes.append(CheckResult("PASS", f"{irq_lines} IRQ lines parsed"))
            passes.append(CheckResult("PASS", f"{len(net_rows1)} interfaces parsed from /proc/net/dev"))

            time.sleep(max(0.2, interval))

            intr2 = _safe_read("/proc/interrupts")
            soft2 = _safe_read("/proc/softirqs")
            net2 = _safe_read("/proc/net/dev")
            if intr2 is None or soft2 is None or net2 is None:
                fails.append(CheckResult("FAIL", "Failed second sampling read"))
            else:
                intr_total2, _, per_irq2, _ = _parse_interrupts(intr2)
                soft_total2, _ = _parse_softirqs(soft2)
                net_rows2, rx_total2, tx_total2 = _parse_netdev(net2)

                if intr_total2 > intr_total1:
                    passes.append(CheckResult("PASS", "IRQ counters changing"))
                else:
                    warns.append(CheckResult("WARN", "IRQ counters not changing during sample window"))

                if soft_total2 > soft_total1:
                    passes.append(CheckResult("PASS", "SoftIRQ counters changing"))
                else:
                    warns.append(CheckResult("WARN", "SoftIRQ counters not changing during sample window"))

                if rx_total2 > rx_total1 or tx_total2 > tx_total1:
                    passes.append(CheckResult("PASS", "Network RX/TX counters changing"))
                else:
                    warns.append(CheckResult("WARN", "Network RX/TX counters not changing during sample window"))

                common_irqs = set(per_irq1.keys()) & set(per_irq2.keys())
                changing_irq_lines = sum(1 for k in common_irqs if per_irq2[k] > per_irq1[k])
                if changing_irq_lines > 0:
                    passes.append(CheckResult("PASS", f"{changing_irq_lines} IRQ lines active during sample"))
                else:
                    warns.append(CheckResult("WARN", "No per-IRQ line activity observed during sample"))

        topo = _collect_topology()
        if topo:
            passes.append(CheckResult("PASS", f"CPU topology detected ({len(topo)} CPU entries)"))
        else:
            warns.append(CheckResult("WARN", "CPU topology not available from /sys"))

        numa = _numa_nodes()
        if numa:
            passes.append(CheckResult("PASS", f"NUMA nodes detected: {', '.join(numa)}"))
        else:
            warns.append(CheckResult("WARN", "NUMA nodes unavailable or single-node system"))

        ethtool_exists = Path("/usr/sbin/ethtool").exists() or Path("/usr/bin/ethtool").exists()
        if ethtool_exists:
            passes.append(CheckResult("PASS", "ethtool available"))
        else:
            warns.append(CheckResult("WARN", "ethtool unavailable"))
    else:
        topo = []
        warns.append(CheckResult("WARN", f"Skipping local Linux source checks in mode={mode} on host_os={platform.system()}"))

    if server_url and sut_id:
        viz = _http_get_json(server_url, f"/api/systems/{urllib.parse.quote(sut_id)}/visualization?window_seconds=60&top_n=20")
        topo_api = _http_get_json(server_url, f"/api/systems/{urllib.parse.quote(sut_id)}/visualization/topology")
        net_api = _http_get_json(server_url, f"/api/network/current?sut_id={urllib.parse.quote(sut_id)}")
        irq_api = _http_get_json(server_url, f"/api/irq/current?sut_id={urllib.parse.quote(sut_id)}&limit=1024")

        if viz is None:
            fails.append(CheckResult("FAIL", "IRQLENS visualization endpoint unreachable or invalid JSON"))
        else:
            passes.append(CheckResult("PASS", "IRQLENS visualization endpoint reachable"))
            cpus = viz.get("cpu_heatmap", {}).get("cpus", [])
            if cpus:
                passes.append(CheckResult("PASS", f"Visualization CPU heatmap populated ({len(cpus)} CPUs)"))
            else:
                warns.append(CheckResult("WARN", "Visualization CPU heatmap has no CPU entries in selected window"))

            irq_values = viz.get("irq_heatmap", {}).get("values", [])
            if irq_values:
                passes.append(CheckResult("PASS", f"IRQ heatmap contains {len(irq_values)} IRQ/CPU data points"))
            else:
                warns.append(CheckResult("WARN", "IRQ heatmap has no points in selected window"))

        if topo_api is None:
            fails.append(CheckResult("FAIL", "IRQLENS topology endpoint unreachable"))
        else:
            if topo_api.get("available") and topo_api.get("rows"):
                passes.append(CheckResult("PASS", f"IRQLENS remote topology available ({len(topo_api.get('rows', []))} rows)"))
                if topo:
                    local_cpus = {row["cpu_id"] for row in topo}
                    api_cpus = {int(row.get("cpu_id", -1)) for row in topo_api.get("rows", []) if row.get("cpu_id") is not None}
                    missing = local_cpus - api_cpus
                    if missing:
                        warns.append(CheckResult("WARN", f"Topology mismatch: {len(missing)} local CPUs missing from IRQLENS topology"))
                    else:
                        passes.append(CheckResult("PASS", "IRQLENS topology CPU IDs match local topology"))
            else:
                warns.append(CheckResult("WARN", "IRQLENS topology marked unavailable"))

        if net_api is None:
            fails.append(CheckResult("FAIL", "IRQLENS network endpoint unreachable"))
        else:
            api_ifaces = {row.get("interface") for row in net_api.get("interfaces", [])}
            if run_local_checks:
                local_ifaces = set(_parse_netdev(_safe_read("/proc/net/dev") or "")[0].keys())
                missing_ifaces = sorted(local_ifaces - api_ifaces)
                if missing_ifaces:
                    warns.append(CheckResult("WARN", "Interfaces missing in IRQLENS network payload: " + ", ".join(missing_ifaces)))
                else:
                    passes.append(CheckResult("PASS", "IRQLENS network interfaces match /proc/net/dev"))
            else:
                if api_ifaces:
                    passes.append(CheckResult("PASS", f"IRQLENS network endpoint returned {len(api_ifaces)} interfaces"))
                else:
                    warns.append(CheckResult("WARN", "IRQLENS network endpoint returned no interfaces"))

        if irq_api is None:
            fails.append(CheckResult("FAIL", "IRQLENS IRQ endpoint unreachable"))
        else:
            rows = irq_api.get("rows", [])
            if rows:
                passes.append(CheckResult("PASS", f"IRQLENS IRQ rows available ({len(rows)} rows)"))
            else:
                warns.append(CheckResult("WARN", "IRQLENS IRQ rows empty for selected SUT"))

    return passes, warns, fails


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate Linux SUT telemetry sources for IRQLENS visualizations")
    ap.add_argument("--server-url", default="", help="Optional IRQLENS server URL, e.g. http://127.0.0.1:8080")
    ap.add_argument("--sut-id", default="", help="SUT ID used by IRQLENS (required with --server-url)")
    ap.add_argument("--sample-interval", type=float, default=1.0, help="Seconds between counter samples")
    ap.add_argument(
        "--mode",
        choices=["auto", "local", "api-only"],
        default="auto",
        help="Validation mode: auto (local on Linux), local (force Linux source checks), api-only (backend payload checks only)",
    )
    args = ap.parse_args()

    passes, warns, fails = run_validation(args.server_url, args.sut_id, args.sample_interval, args.mode)

    for row in passes:
        print(_status_line(row.status, row.message))
    for row in warns:
        print(_status_line(row.status, row.message))
    for row in fails:
        print(_status_line(row.status, row.message))

    print("----")
    print(f"PASS={len(passes)} WARN={len(warns)} FAIL={len(fails)}")

    if fails:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
