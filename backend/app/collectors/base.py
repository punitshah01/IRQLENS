from __future__ import annotations

import time
from pathlib import Path
from typing import Optional


class CollectorError(Exception):
    pass


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
