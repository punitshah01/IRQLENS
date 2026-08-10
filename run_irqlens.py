#!/usr/bin/env python3
from __future__ import annotations

import socket
import os
import subprocess
import sys
from pathlib import Path


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


def _print_banner(url: str) -> None:
    lines = [
        "  ┌──────────────────────────────────────────────┐",
        "  │  IRQLENS ready                               │",
        "  │                                              │",
        f"  │  {url.ljust(42)}│",
        "  │                                              │",
        "  │  Paste the URL above into your browser.      │",
        "  │  Press Ctrl+C to stop.                       │",
        "  └──────────────────────────────────────────────┘",
    ]
    print("\n".join(lines))


def main() -> int:
    root = Path(__file__).resolve().parent
    backend = root / "backend"
    req = backend / "requirements.txt"

    if not req.exists():
        print("Error: backend/requirements.txt not found.")
        return 1

    venv_py = _venv_python(backend)
    if not venv_py.exists():
        subprocess.check_call([sys.executable, "-m", "venv", str(backend / ".venv")])

    subprocess.check_call([str(venv_py), "-m", "pip", "install", "-r", str(req)])

    env = dict(os.environ)
    env.setdefault("IRQLENS_DB_PATH", str((backend / "data" / "irqlens.db").resolve()))
    host_ip = _detect_host_ip()
    url = f"http://{host_ip}:8080"

    cmd = [
        str(venv_py),
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        "0.0.0.0",
        "--port",
        "8080",
    ]
    print("Starting IRQLENS")
    _print_banner(url)
    return subprocess.call(cmd, cwd=str(backend), env=env)


if __name__ == "__main__":
    raise SystemExit(main())
