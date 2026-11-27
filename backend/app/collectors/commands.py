from __future__ import annotations

import re
import shutil
import subprocess
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, List

COMMAND_CATEGORIES = frozenset({"network", "interfaces", "routes", "sockets", "ethtool", "storage"})
# `ethtool -S` on a NIC with 126 queues prints about 110,000 characters.
MAX_STREAM_CHARS = 1024 * 1024


# Standard library only: the collectors also run on machines that have nothing but Python.
@dataclass
class CommandResult:
    timestamp: float
    category: str
    command: str
    exit_code: int
    stdout: str
    stderr: str
    success: bool
    duration_ms: float
    interface: str = ""

    def model_dump(self) -> Dict[str, Any]:
        # Same keys in the same order as the server's model, so exported columns stay where they were.
        names = ("timestamp", "category", "command", "interface", "exit_code", "stdout", "stderr", "success", "duration_ms")
        return {name: getattr(self, name) for name in names}


def nvme_controllers(sys_root: str = "/sys") -> List[str]:
    try:
        names = [entry.name for entry in (Path(sys_root) / "class" / "nvme").iterdir()]
    except OSError:
        return []
    return sorted(name for name in names if re.fullmatch(r"nvme\d+", name))


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
                add("ethtool", ["ethtool", "-l", iface])
                add("ethtool", ["ethtool", "-x", iface])
                add("ethtool", ["ethtool", "-c", iface])

        add("storage", ["lsblk", "-J"])
        add("storage", ["nvme", "list", "-o", "json"])
        if "storage" in categories:
            for controller in nvme_controllers():
                add("storage", ["nvme", "smart-log", f"/dev/{controller}"])

        return cmds

    def collect(self, interfaces: List[str], categories: List[str]) -> List[CommandResult]:
        commands = self.available_commands(interfaces, categories)
        results: List[CommandResult] = []
        deadline = time.monotonic() + 120.0
        output_size = 0
        for category in categories:
            if not commands.get(category):
                results.append(CommandResult(
                    timestamp=time.time(), category=category, command=category,
                    exit_code=127, stdout="", stderr="Required diagnostic tool or interface is unavailable.",
                    success=False, duration_ms=0.0,
                ))
            for command in commands.get(category, []):
                if time.monotonic() >= deadline or len(results) >= 500 or output_size >= 4 * 1024 * 1024:
                    results.append(CommandResult(
                        timestamp=time.time(), category=category, command="capture limit",
                        exit_code=124, stdout="", stderr="Diagnostic capture time, command, or output limit reached.",
                        success=False, duration_ms=0.0,
                    ))
                    return results
                interface = command[-1] if command[0] == "ethtool" else ""
                result = self.run(category, command, interface)
                if len(result.stdout) > MAX_STREAM_CHARS or len(result.stderr) > MAX_STREAM_CHARS:
                    result = replace(
                        result,
                        stdout=result.stdout[:MAX_STREAM_CHARS],
                        stderr=result.stderr[:MAX_STREAM_CHARS] + f"\nOutput truncated at {MAX_STREAM_CHARS} characters per stream.",
                        success=False,
                    )
                output_size += len(result.stdout.encode()) + len(result.stderr.encode())
                results.append(result)
        return results

    def run(self, category: str, command: List[str], interface: str = "") -> CommandResult:
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
            return CommandResult(
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
            return CommandResult(
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
            return CommandResult(
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
