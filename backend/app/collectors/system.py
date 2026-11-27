from __future__ import annotations

import os
import platform
import re
import time
from pathlib import Path
from typing import Dict, List

from .base import read_first_line, read_text_safe

_IRQBALANCE_TTL = 30.0


def irqbalance_status(proc_root: str = "/proc") -> str:
    try:
        entries = [entry for entry in Path(proc_root).iterdir() if entry.name.isdigit()]
    except OSError:
        return "unknown"
    if not entries:
        return "unknown"
    for entry in entries:
        if read_first_line(str(entry / "comm"), "") == "irqbalance":
            return "active"
    return "inactive"


def collect_cpu_topology(cpu_model: str = "", sys_root: str = "/sys") -> List[Dict[str, object]]:
    root = Path(sys_root) / "devices" / "system" / "cpu"
    if not root.exists():
        return []
    rows: List[Dict[str, object]] = []
    for cpu_dir in sorted(root.glob("cpu[0-9]*"), key=lambda p: int(p.name[3:])):
        topo = cpu_dir / "topology"
        socket_raw = read_first_line(str(topo / "physical_package_id"), "")
        core_raw = read_first_line(str(topo / "core_id"), "")
        numa_node = None
        for node in cpu_dir.glob("node*"):
            if node.name[4:].isdigit():
                numa_node = int(node.name[4:])
                break
        online_raw = read_first_line(str(cpu_dir / "online"), "")
        freq_khz = None
        for freq_path in (cpu_dir / "cpufreq" / "scaling_cur_freq", cpu_dir / "cpufreq" / "cpuinfo_cur_freq"):
            raw = read_first_line(str(freq_path), "")
            if raw.isdigit():
                freq_khz = int(raw)
                break
        rows.append({
            "cpu_id": int(cpu_dir.name[3:]),
            "socket_id": int(socket_raw) if socket_raw.isdigit() else None,
            "core_id": int(core_raw) if core_raw.isdigit() else None,
            "numa_node": numa_node,
            "online": online_raw == "1" if online_raw in ("0", "1") else None,
            "thread_siblings_list": read_first_line(str(topo / "thread_siblings_list"), ""),
            "core_siblings_list": read_first_line(str(topo / "core_siblings_list"), ""),
            "cpu_model": cpu_model,
            "freq_khz": freq_khz,
        })
    return rows


class SystemCollector:
    def __init__(self) -> None:
        self._irqbalance = "unknown"
        self._irqbalance_at = -_IRQBALANCE_TTL

    def irqbalance(self) -> str:
        now = time.monotonic()
        if now - self._irqbalance_at >= _IRQBALANCE_TTL:
            self._irqbalance = irqbalance_status()
            self._irqbalance_at = now
        return self._irqbalance

    def collect(self, ts: float) -> Dict[str, object]:
        os_dist = "Unknown"
        os_ver = "Unknown"
        os_release = Path("/etc/os-release")
        if os_release.exists():
            text = os_release.read_text(encoding="utf-8", errors="ignore")
            for line in text.splitlines():
                if line.startswith("NAME=") and os_dist == "Unknown":
                    os_dist = line.split("=", 1)[1].strip().strip('"')
                if line.startswith("VERSION=") and os_ver == "Unknown":
                    os_ver = line.split("=", 1)[1].strip().strip('"')

        uptime_seconds = 0.0
        uptime_text = read_text_safe("/proc/uptime").strip().split()
        if uptime_text:
            try:
                uptime_seconds = float(uptime_text[0])
            except ValueError:
                uptime_seconds = 0.0

        boot_time = max(0.0, time.time() - uptime_seconds)

        load1 = load5 = load15 = 0.0
        load_text = read_text_safe("/proc/loadavg").strip().split()
        if len(load_text) >= 3:
            try:
                load1 = float(load_text[0])
                load5 = float(load_text[1])
                load15 = float(load_text[2])
            except ValueError:
                pass

        cpu_model = "Unknown"
        cpu_mhz_values = []
        for line in read_text_safe("/proc/cpuinfo").splitlines():
            if line.lower().startswith("model name"):
                cpu_model = line.split(":", 1)[1].strip()
            elif line.lower().startswith("cpu mhz"):
                try:
                    cpu_mhz_values.append(float(line.split(":", 1)[1].strip()))
                except Exception:
                    continue
        cpu_mhz = (sum(cpu_mhz_values) / len(cpu_mhz_values)) if cpu_mhz_values else None

        mem_total = 0
        mem_avail = 0
        for line in read_text_safe("/proc/meminfo").splitlines():
            if line.startswith("MemTotal:"):
                parts = re.findall(r"\d+", line)
                mem_total = int(parts[0]) if parts else 0
            elif line.startswith("MemAvailable:"):
                parts = re.findall(r"\d+", line)
                mem_avail = int(parts[0]) if parts else 0

        numa_nodes = 0
        nodes_dir = Path("/sys/devices/system/node")
        if nodes_dir.exists():
            numa_nodes = len([x for x in nodes_dir.iterdir() if x.name.startswith("node")])

        return {
            "timestamp": ts,
            "hostname": platform.node() or "unknown",
            "kernel": platform.release(),
            "os_distribution": os_dist,
            "os_version": os_ver,
            "uptime_seconds": uptime_seconds,
            "boot_time_epoch": boot_time,
            "loadavg_1m": load1,
            "loadavg_5m": load5,
            "loadavg_15m": load15,
            "cpu_count": os.cpu_count() or 0,
            "cpu_model": cpu_model,
            "cpu_mhz": cpu_mhz,
            "memory_total_kb": mem_total,
            "memory_available_kb": mem_avail,
            "numa_nodes": numa_nodes,
            "running_as_root": os.geteuid() == 0 if hasattr(os, "geteuid") else False,
            "irqbalance": self.irqbalance(),
        }
