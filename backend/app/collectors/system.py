from __future__ import annotations

import os
import platform
import re
import time
from pathlib import Path
from typing import Dict

from .base import read_text_safe


class SystemCollector:
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
        }
