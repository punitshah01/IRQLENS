from __future__ import annotations

import json
import logging
import re
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional

logger = logging.getLogger("irqlens.remote")

REPO_ROOT = Path(__file__).resolve().parents[3]
# The whole remote command. The code it runs arrives on stdin, so nothing is installed or written on the machine.
LOADER = "import sys;exec(sys.stdin.buffer.read(int(sys.stdin.buffer.readline())))"
REMOTE_COMMAND = f"python3 -u -c '{LOADER}'"
# Runs on the machine: imports come from the sources sent along, then the agent talks over stdin and stdout.
BOOTSTRAP = """
import importlib.abc, importlib.util, sys

class _Sent(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, name, path=None, target=None):
        if name in MODULES:
            return importlib.util.spec_from_loader(name, self, is_package=MODULES[name][0])
    def create_module(self, spec):
        return None
    def exec_module(self, module):
        package, source = MODULES[module.__name__]
        module.__file__ = "<irqlens>/" + module.__name__.replace(".", "/") + ("/__init__.py" if package else ".py")
        exec(compile(source, module.__file__, "exec"), module.__dict__)

if sys.version_info < (3, 9):
    sys.exit("IRQLENS needs Python 3.9 or newer; python3 on this machine is %d.%d." % sys.version_info[:2])
sys.meta_path.insert(0, _Sent())
from agent.main import run_stdio
run_stdio(**CONFIG)
"""
TARGET = re.compile(r"(?:(?P<user>[A-Za-z_][A-Za-z0-9_.-]{0,63})@)?(?P<host>[A-Za-z0-9][A-Za-z0-9._-]{0,252})(?::(?P<port>\d{1,5}))?")
RETRY_SECONDS = (5.0, 10.0, 30.0, 60.0)
JOB_POLL_SECONDS = 2.0


@dataclass
class Machine:
    id: str
    host: str
    user: str
    port: int
    role: str
    # connecting, online or error
    state: str = "connecting"
    message: str = ""
    stop: threading.Event = field(default_factory=threading.Event, repr=False)
    process: Optional[subprocess.Popen] = field(default=None, repr=False)
    thread: Optional[threading.Thread] = field(default=None, repr=False)

    @property
    def target(self) -> str:
        return (f"{self.user}@" if self.user else "") + self.host + (f":{self.port}" if self.port != 22 else "")

    def hint(self) -> str:
        login = (f"-p {self.port} " if self.port != 22 else "") + (f"{self.user}@" if self.user else "") + self.host
        if "Permission denied" in self.message:
            return f"The SSH key of the IRQLENS server is not accepted there. On the server run: ssh-copy-id {login}"
        if "Host key verification failed" in self.message:
            return f"That machine's host key is not the one this server knows. If that is expected, on the server run: ssh-keygen -R {self.host}"
        if "python3" in self.message and "not found" in self.message:
            return "Install python3 (3.9 or newer) on that machine."
        return ""

    def describe(self) -> Dict[str, Any]:
        return {"id": self.id, "target": self.target, "role": self.role, "state": self.state, "message": self.message, "hint": self.hint()}


def parse_target(text: str, role: str = "client") -> Machine:
    match = TARGET.fullmatch(text.strip())
    port = int(match["port"] or 22) if match else 0
    if not match or not 1 <= port <= 65535:
        raise ValueError(f"'{text.strip()[:80]}' is not a machine address. Use [user@]host[:port], for example root@192.0.2.35.")
    if role not in ("client", "server"):
        raise ValueError("role must be client or server")
    host = match["host"].lower()
    machine_id = host if port == 22 else f"{host}:{port}"
    if machine_id == "local":
        raise ValueError("'local' is the machine IRQLENS runs on; it is monitored already.")
    return Machine(id=machine_id, host=host, user=match["user"] or "", port=port, role=role)


def ssh_command(machine: Machine) -> List[str]:
    # An argument list, never a shell string; the validated host follows "--", so it cannot be read as an option.
    return [
        "ssh", "-T", "-p", str(machine.port),
        # Key login only: a password prompt would hang the connection.
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
        # A machine seen for the first time is accepted; one whose key changed is refused.
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ForwardAgent=no", "-o", "ForwardX11=no", "-o", "ClearAllForwardings=yes", "-o", "LogLevel=ERROR",
        *(["-l", machine.user] if machine.user else []),
        "--", machine.host, REMOTE_COMMAND,
    ]


@lru_cache(maxsize=1)
def _sources() -> Dict[str, tuple]:
    # Module name -> (is a package, source): what the agent imports, read once from this checkout.
    modules: Dict[str, tuple] = {"backend": (True, ""), "backend.app": (True, ""), "agent": (True, "")}
    for path in sorted((REPO_ROOT / "backend" / "app" / "collectors").glob("*.py")):
        package = path.stem == "__init__"
        modules["backend.app.collectors" + ("" if package else f".{path.stem}")] = (package, path.read_text(encoding="utf-8"))
    modules["agent.main"] = (False, (REPO_ROOT / "agent" / "main.py").read_text(encoding="utf-8"))
    return modules


def bootstrap(config: Dict[str, Any]) -> bytes:
    # What the loader reads: one line with the length, then that many bytes of Python.
    source = f"MODULES = {_sources()!r}\nCONFIG = {config!r}\n{BOOTSTRAP}".encode("utf-8")
    return str(len(source)).encode() + b"\n" + source


def _keep_last_lines(stream: Any, lines: Deque[str]) -> None:
    for raw in stream:
        text = raw.decode("utf-8", errors="replace").strip()
        if text:
            lines.append(text[:500])


# Machines monitored over SSH: one connection and one thread each, for as long as the server runs.
class RemoteMachines:
    def __init__(
        self,
        on_message: Callable[[str, Dict[str, Any]], None],
        next_job: Callable[[str], Optional[Dict[str, Any]]],
        telemetry_interval: float,
        command: Callable[[Machine], List[str]] = ssh_command,
    ) -> None:
        self._on_message = on_message
        self._next_job = next_job
        self._telemetry_interval = telemetry_interval
        self._command = command
        self._machines: Dict[str, Machine] = {}
        self._lock = threading.Lock()
        self.retry_seconds = RETRY_SECONDS

    def add(self, machine: Machine) -> Machine:
        with self._lock:
            if machine.id in self._machines:
                raise ValueError(f"{machine.id} is already being monitored.")
            self._machines[machine.id] = machine
        machine.thread = threading.Thread(target=self._run, args=(machine,), name=f"irqlens-ssh-{machine.id}", daemon=True)
        machine.thread.start()
        return machine

    def remove(self, machine_id: str) -> bool:
        with self._lock:
            machine = self._machines.pop(machine_id, None)
        if not machine:
            return False
        machine.stop.set()
        self._close(machine)
        # Wait for the last message in flight, so nothing reports for this machine after it is gone.
        if machine.thread and machine.thread is not threading.current_thread():
            machine.thread.join(timeout=5.0)
        return True

    def stop(self) -> None:
        for machine_id in list(self._machines):
            self.remove(machine_id)

    def describe(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [machine.describe() for machine in self._machines.values()]

    def _run(self, machine: Machine) -> None:
        failures = 0
        while not machine.stop.is_set():
            machine.state = "connecting"
            reason = self._session(machine)
            if machine.stop.is_set():
                return
            # A connection that worked is retried soon; one that keeps failing, less and less often.
            failures = 0 if machine.state == "online" else failures + 1
            delay = self.retry_seconds[min(max(failures - 1, 0), len(self.retry_seconds) - 1)]
            machine.state, machine.message = "error", reason
            logger.warning("machine %s: %s (next attempt in %.0f s)", machine.target, reason, delay)
            machine.stop.wait(delay)

    def _session(self, machine: Machine) -> str:
        try:
            process = subprocess.Popen(self._command(machine), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError as error:
            return f"The IRQLENS server could not start ssh: {error}"
        machine.process = process
        errors: Deque[str] = deque(maxlen=3)
        drain = threading.Thread(target=_keep_last_lines, args=(process.stderr, errors), daemon=True)
        drain.start()
        failure = ""
        try:
            process.stdin.write(bootstrap({"sut_id": machine.id, "role": machine.role, "telemetry_interval": self._telemetry_interval}))
            process.stdin.flush()
            polled_at = 0.0
            for line in process.stdout:
                try:
                    message = json.loads(line)
                except ValueError:
                    # Not ours, for example a greeting printed by the login shell.
                    continue
                if not isinstance(message, dict) or machine.stop.is_set():
                    continue
                self._on_message(machine.id, message)
                if machine.state != "online":
                    machine.state, machine.message = "online", ""
                    logger.info("machine %s is reporting", machine.target)
                if time.monotonic() - polled_at >= JOB_POLL_SECONDS:
                    polled_at = time.monotonic()
                    job = self._next_job(machine.id)
                    if job:
                        process.stdin.write(json.dumps(job).encode() + b"\n")
                        process.stdin.flush()
        except Exception as error:
            # A closed pipe, or data this server could not store.
            failure = f"{type(error).__name__}: {error}"[:500]
        finally:
            self._close(machine)
            try:
                process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                process.kill()
            drain.join(timeout=2.0)
            process.stdout.close()
        # What ssh or the machine said last explains more than a closed pipe does.
        return (errors[-1] if errors else "") or failure or f"The connection closed (ssh exit code {process.returncode})."

    @staticmethod
    def _close(machine: Machine) -> None:
        process = machine.process
        if not process:
            return
        # Closing stdin ends the agent on the machine; ending ssh covers a connection that hangs.
        for action in (process.stdin.close, process.terminate):
            try:
                action()
            except (OSError, ValueError):
                pass
