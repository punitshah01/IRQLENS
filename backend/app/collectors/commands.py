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

    def collect(self, interfaces: List[str], categories: List[str]) -> List[DiagnosticCommandResult]:
        commands = self.available_commands(interfaces, categories)
        results: List[DiagnosticCommandResult] = []
        deadline = time.monotonic() + 120.0
        output_size = 0
        for category in categories:
            if not commands.get(category):
                results.append(DiagnosticCommandResult(
                    timestamp=time.time(), category=category, command=category,
                    exit_code=127, stdout="", stderr="Required diagnostic tool or interface is unavailable.",
                    success=False, duration_ms=0.0,
                ))
            for command in commands.get(category, []):
                if time.monotonic() >= deadline or len(results) >= 500 or output_size >= 4 * 1024 * 1024:
                    results.append(DiagnosticCommandResult(
                        timestamp=time.time(), category=category, command="capture limit",
                        exit_code=124, stdout="", stderr="Diagnostic capture time, command, or output limit reached.",
                        success=False, duration_ms=0.0,
                    ))
                    return results
                interface = command[-1] if command[0] == "ethtool" else ""
                result = self.run(category, command, interface)
                if len(result.stdout) > 65536 or len(result.stderr) > 65536:
                    result = result.model_copy(update={
                        "stdout": result.stdout[:65536], "stderr": result.stderr[:65536] + "\nOutput truncated at 65536 characters per stream.",
                        "success": False,
                    })
                output_size += len(result.stdout.encode()) + len(result.stderr.encode())
                results.append(result)
        return results

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
            stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            return DiagnosticCommandResult(
                timestamp=time.time(),
                category=category,
                command=" ".join(command),
                interface=interface,
                exit_code=124,
                stdout=stdout,
                stderr=stderr + "\ncommand timed out",
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
