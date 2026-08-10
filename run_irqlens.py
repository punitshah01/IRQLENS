#!/usr/bin/env python3
from __future__ import annotations

import argparse
import socket
import os
import subprocess
import sys
import time
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


def _default_sut_id(host_ip: str) -> str:
    try:
        return socket.gethostname() or host_ip
    except Exception:
        return host_ip


def _merge_allowlist_for_local_collection(env: dict[str, str], host_ip: str) -> None:
    entries = []
    raw = env.get("IRQLENS_ALLOWED_INGEST_IPS", "")
    if raw:
        entries.extend([x.strip() for x in raw.split(",") if x.strip()])
    for candidate in [host_ip, "127.0.0.1", "::1", "localhost"]:
        if candidate and candidate not in entries:
            entries.append(candidate)
    env["IRQLENS_ALLOWED_INGEST_IPS"] = ",".join(entries)


def main() -> int:
    ap = argparse.ArgumentParser(description="Start IRQLENS backend and optionally local collector")
    ap.add_argument("--collect-local", action="store_true", help="Also start the local IRQ collector on this Linux host")
    ap.add_argument("--nic", default="", help="NIC name for local collection, e.g. ens3np0")
    ap.add_argument("--sut-ip", default="", help="Logical SUT identifier shown in the dashboard")
    ap.add_argument("--interval", type=float, default=1.0, help="Collector sampling interval seconds")
    ap.add_argument("--topn", type=int, default=64, help="Top IRQ lines sent per sample")
    args = ap.parse_args()

    root = Path(__file__).resolve().parent
    backend = root / "backend"
    collector = root / "collector" / "irq_collector.py"
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
    if args.collect_local:
        env["IRQLENS_DISABLE_INGEST_ALLOWLIST"] = "1"

    backend_cmd = [
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

    backend_proc = subprocess.Popen(backend_cmd, cwd=str(backend), env=env)
    collector_proc = None
    try:
        if args.collect_local:
            if os.name == "nt":
                print("Local collection is only supported on Linux because IRQLENS reads /proc telemetry.")
            else:
                time.sleep(1.0)
                sut_id = args.sut_ip or _default_sut_id(host_ip)
                collector_cmd = [
                    sys.executable,
                    str(collector),
                    "--server",
                    url,
                    "--sut-ip",
                    sut_id,
                    "--interval",
                    str(args.interval),
                    "--topn",
                    str(args.topn),
                ]
                if args.nic:
                    collector_cmd.extend(["--nic", args.nic])
                print(f"Starting local collector for {sut_id}")
                collector_proc = subprocess.Popen(collector_cmd, cwd=str(root), env=env)

        return backend_proc.wait()
    except KeyboardInterrupt:
        return 130
    finally:
        if collector_proc and collector_proc.poll() is None:
            collector_proc.terminate()
        if backend_proc.poll() is None:
            backend_proc.terminate()


if __name__ == "__main__":
    raise SystemExit(main())
