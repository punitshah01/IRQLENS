from __future__ import annotations

import os
import time
from pathlib import Path
from typing import List, Optional


class CollectorError(Exception):
    pass


def read_int(path: object, default: Optional[int] = None) -> Optional[int]:
    try:
        return int(read_first_line(str(path), ""))
    except ValueError:
        return default


def list_msi_irqs(device_dir: object) -> List[str]:
    try:
        names = [child.name for child in (Path(str(device_dir)) / "msi_irqs").iterdir() if child.name.isdigit()]
    except OSError:
        return []
    return sorted(names, key=int)


def link_name(path: object, default: str = "N/A") -> str:
    try:
        return os.path.basename(os.readlink(str(path))) or default
    except (OSError, ValueError):
        pass
    # A plain file holding the target name stands in where symlinks are unavailable.
    if Path(str(path)).is_file():
        return read_first_line(str(path), default)
    return default


def read_text_safe(path: str) -> str:
    p = Path(path)
    if not p.exists():
        return ""
    try:
        return p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def read_first_line(path: str, default: str = "N/A") -> str:
    text = read_text_safe(path)
    if not text:
        return default
    line = text.splitlines()[0].strip()
    return line if line else default


def monotonic_interval(previous: Optional[float], now: Optional[float] = None, minimum: float = 1e-3) -> float:
    ts = time.monotonic() if now is None else now
    if previous is None:
        return minimum
    return max(minimum, ts - previous)
