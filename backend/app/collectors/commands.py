from __future__ import annotations

import shutil
import subprocess
import time
from typing import Dict, List

from ..models import DiagnosticCommandResult


class DiagnosticCommandCollector:
    def __init__(self, timeout_seconds: float) -> None:
        self.timeout_seconds = timeout_seconds

    def command_available(self, executable: str) -> bool:
        return shutil.which(executable) is not None

    def available_commands(self, interfaces: List[str], categories: List[str]) -> Dict[str, List[List[str]]]:
        cmds: Dict[str, List[List[str]]] = {}

        def add(category: str, command: List[str]) -> None:
            if category not in categories:
                return
            exe = command[0]
            if self.command_available(exe):
                cmds.setdefault(category, []).append(command)

        add("network", ["ip", "addr"])
        add("network", ["ip", "-br", "addr"])
        add("interfaces", ["ip", "link"])
        add("interfaces", ["ip", "-s", "link"])
        add("routes", ["ip", "route"])
        add("routes", ["ip", "-6", "route"])
        add("routes", ["ip", "rule"])
        add("network", ["ip", "neigh"])
        add("sockets", ["ss", "-s"])
        add("sockets", ["ss", "-tuna"])
        add("sockets", ["ss", "-lntup"])

        add("network", ["sysctl", "-a"])

        if "ethtool" in categories and self.command_available("ethtool"):
            for iface in interfaces:
                add("ethtool", ["ethtool", iface])
                add("ethtool", ["ethtool", "-i", iface])
                add("ethtool", ["ethtool", "-k", iface])
                add("ethtool", ["ethtool", "-S", iface])
                add("ethtool", ["ethtool", "-g", iface])
                add("ethtool", ["ethtool", "-c", iface])

        return cmds

    def run(self, category: str, command: List[str], interface: str = "") -> DiagnosticCommandResult:
        started = time.time()
        try:
            proc = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
            return DiagnosticCommandResult(
                timestamp=time.time(),
                category=category,
                command=" ".join(command),
                interface=interface,
                exit_code=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
                success=(proc.returncode == 0),
                duration_ms=(time.time() - started) * 1000.0,
            )
        except subprocess.TimeoutExpired as exc:
            return DiagnosticCommandResult(
                timestamp=time.time(),
                category=category,
                command=" ".join(command),
                interface=interface,
                exit_code=124,
                stdout=exc.stdout or "",
                stderr=(exc.stderr or "") + "\ncommand timed out",
                success=False,
                duration_ms=(time.time() - started) * 1000.0,
            )
        except Exception as exc:
            return DiagnosticCommandResult(
                timestamp=time.time(),
                category=category,
                command=" ".join(command),
                interface=interface,
                exit_code=1,
                stdout="",
                stderr=str(exc),
                success=False,
                duration_ms=(time.time() - started) * 1000.0,
            )
