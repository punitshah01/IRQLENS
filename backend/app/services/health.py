from __future__ import annotations

import os
import platform
import shutil
import time
from pathlib import Path
from typing import List

from ..config import Settings
from ..models import AppDependencyStatus, HealthStatus


class HealthService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def dependencies(self) -> List[AppDependencyStatus]:
        deps = ["ip", "ss", "ethtool", "sysctl", "lscpu", "numactl"]
        out: List[AppDependencyStatus] = []
        for dep in deps:
            exe = shutil.which(dep)
            out.append(AppDependencyStatus(name=dep, available=bool(exe), detail=exe or "Missing"))
        out.append(AppDependencyStatus(name="python", available=True, detail=platform.python_version()))
        return out

    def os_info(self) -> tuple[str, str]:
        dist = "Unknown"
        ver = "Unknown"
        path = Path("/etc/os-release")
        if path.exists():
            text = path.read_text(encoding="utf-8", errors="ignore")
            for line in text.splitlines():
                if line.startswith("NAME=") and dist == "Unknown":
                    dist = line.split("=", 1)[1].strip().strip('"')
                if line.startswith("VERSION=") and ver == "Unknown":
                    ver = line.split("=", 1)[1].strip().strip('"')
        return dist, ver

    def build(self, collector_status: str, db_ok: bool, ws_status: str) -> HealthStatus:
        os_dist, _ = self.os_info()
        uptime_seconds = 0.0
        try:
            with open("/proc/uptime", "r", encoding="utf-8") as fh:
                uptime_seconds = float(fh.read().split()[0])
        except Exception:
            pass

        return HealthStatus(
            ok=(collector_status != "failed" and db_ok),
            application="running",
            collector_status=collector_status,
            database_status="ok" if db_ok else "failed",
            websocket_status=ws_status,
            hostname=platform.node() or "unknown",
            os_distribution=os_dist,
            kernel=platform.release(),
            uptime_seconds=uptime_seconds,
            running_as_root=(os.geteuid() == 0 if hasattr(os, "geteuid") else False),
            dependencies=self.dependencies(),
            interval_seconds=self.settings.collection_interval,
        )
