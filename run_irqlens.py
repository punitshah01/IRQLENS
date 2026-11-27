#!/usr/bin/env python3
from __future__ import annotations

import argparse
import socket
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
REQUIRED_MODULES = "fastapi, uvicorn, pydantic, websockets, openpyxl"


def _venv_python(backend_dir: Path) -> Path:
    if os.name == "nt":
        return backend_dir / ".venv" / "Scripts" / "python.exe"
    return backend_dir / ".venv" / "bin" / "python"


def _detect_host_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        return ip if ip else "127.0.0.1"
    except Exception:
        return "127.0.0.1"
    finally:
        sock.close()


def _print_banner(url: str, hint: str) -> None:
    lines = [
        "  ┌──────────────────────────────────────────────┐",
        "  │  IRQLENS ready                               │",
        "  │                                              │",
        f"  │  {url.ljust(44)}│",
        "  │                                              │",
        f"  │  {hint.ljust(44)}│",
        "  │  Press Ctrl+C to stop.                       │",
        "  └──────────────────────────────────────────────┘",
    ]
    print("\n".join(lines))


def _dependency_status() -> list[tuple[str, str]]:
    deps = ["ip", "ss", "ethtool", "sysctl", "lscpu", "numactl", "lsblk", "nvme"]
    out: list[tuple[str, str]] = []
    for dep in deps:
        exe = shutil.which(dep)
        out.append((dep, exe if exe else "Missing"))
    return out


def _running_as_root() -> bool:
    if hasattr(os, "geteuid"):
        return os.geteuid() == 0
    return False


def _dependencies_installed(venv_py: Path) -> bool:
    check = subprocess.run([str(venv_py), "-c", f"import {REQUIRED_MODULES}"], capture_output=True)
    return check.returncode == 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Start the IRQLENS server; local collection is built in")
    ap.add_argument("--host", default="0.0.0.0", help="Bind address (default 0.0.0.0 = all interfaces; 127.0.0.1 = this host only)")
    ap.add_argument("--port", type=int, default=10000, help="Bind port")
    ap.add_argument("--remote", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--token", default="", help="Require this token from every browser, script, and agent (default: IRQLENS_TOKEN; unset = no sign-in)")
    ap.add_argument("--interval", type=float, default=0.0, help="Local sampling interval in seconds")
    args = ap.parse_args()

    root = Path(__file__).resolve().parent
    backend = root / "backend"
    req = backend / "requirements.txt"

    if not req.exists():
        print("Error: backend/requirements.txt not found.")
        return 1

    venv_py = _venv_python(backend)
    if not venv_py.exists():
        subprocess.check_call([sys.executable, "-m", "venv", str(backend / ".venv")])

    # Skipping pip when everything imports keeps restarts fast and works without network access.
    if not _dependencies_installed(venv_py):
        # Wheels only: without one for this interpreter pip would try to compile pydantic-core (needs Rust).
        install = subprocess.run([str(venv_py), "-m", "pip", "install", "--only-binary=:all:", "-r", str(req)])
        if install.returncode != 0:
            version = subprocess.run([str(venv_py), "-V"], capture_output=True, text=True).stdout.strip()
            print(f"Error: dependency install failed in backend/.venv ({version}); see the pip output above.")
            print("IRQLENS needs prebuilt wheels: use Python 3.9-3.14 (delete backend/.venv after switching) and check access to PyPI.")
            return 1

    host = args.host
    local = host in LOOPBACK_HOSTS

    env = dict(os.environ)
    env.setdefault("IRQLENS_DB_PATH", str((backend / "data" / "irqlens.db").resolve()))
    env.setdefault("IRQLENS_OUTPUT_DIR", str(root / "captures"))
    env["IRQLENS_HOST"] = host
    env["IRQLENS_PORT"] = str(args.port)
    if args.interval > 0:
        env["IRQLENS_COLLECTION_INTERVAL"] = str(args.interval)

    token = args.token or env.get("IRQLENS_TOKEN", "")
    if token:
        env["IRQLENS_TOKEN"] = token
    protected = bool(token or env.get("IRQLENS_API_TOKEN") or env.get("IRQLENS_AGENT_TOKEN"))

    host_ip = "127.0.0.1" if local else _detect_host_ip()
    url = f"http://{host_ip}:{args.port}"
    no_proxy_hosts = [host_ip, "127.0.0.1", "::1", "localhost"]
    existing_no_proxy = env.get("no_proxy") or env.get("NO_PROXY") or ""
    entries = [x.strip() for x in existing_no_proxy.split(",") if x.strip()]
    for candidate in no_proxy_hosts:
        if candidate not in entries:
            entries.append(candidate)
    env["no_proxy"] = ",".join(entries)
    env["NO_PROXY"] = env["no_proxy"]

    backend_cmd = [
        str(venv_py),
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        host,
        "--port",
        str(args.port),
    ]
    print("Starting IRQLENS")
    print(f"Platform: {platform.system()} {platform.release()}")
    print(f"Running as root: {'YES' if _running_as_root() else 'NO'}")
    print(f"Captures: {env['IRQLENS_OUTPUT_DIR']}")
    print("Dependency status:")
    for name, status in _dependency_status():
        print(f"  - {name}: {status}")
    if args.remote:
        print("Note: --remote is no longer needed; all interfaces are served by default.")
    hint = "Paste the URL above into your browser."
    if local:
        print("This host only: listening on loopback.")
        print(f"  From another machine: ssh -L {args.port}:127.0.0.1:{args.port} <user>@<this-host>, then open {url} there.")
        hint = "Works in a browser on this host only."
    elif protected:
        print(f"Listening on {host}:{args.port}; sign-in required.")
        print("  Browser sign-in: user 'irqlens', password = token")
        print(f"  Agent: python3 agent/main.py --server {url} --token <token>")
    else:
        print(f"Listening on {host}:{args.port} without sign-in: anyone who can reach this port can use the dashboard.")
        print("  --token <value> requires sign-in; --host 127.0.0.1 serves this host only.")
    _print_banner(url, hint)
    # Redirected output is block-buffered; flush so the URL reaches the log before the server starts.
    sys.stdout.flush()

    backend_proc = subprocess.Popen(backend_cmd, cwd=str(backend), env=env)
    try:
        return backend_proc.wait()
    except KeyboardInterrupt:
        return 130
    finally:
        if backend_proc.poll() is None:
            backend_proc.terminate()


if __name__ == "__main__":
    raise SystemExit(main())
