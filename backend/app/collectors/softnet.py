from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from .base import read_text_safe

FIELDS = ("processed", "dropped", "time_squeeze", "cpu_collision")
_COLUMNS = {"processed": 0, "dropped": 1, "time_squeeze": 2, "cpu_collision": 8}
_CPU_ID_COLUMN = 12


def parse_softnet_stat(text: str) -> Dict[str, Dict[str, int]]:
    rows: Dict[str, Dict[str, int]] = {}
    for index, line in enumerate(text.splitlines()):
        parts = line.split()
        if len(parts) < 3:
            continue
        try:
            values = [int(part, 16) for part in parts]
        except ValueError:
            continue
        # Kernels without the CPU-id column list online CPUs in order.
        cpu = str(values[_CPU_ID_COLUMN]) if len(values) > _CPU_ID_COLUMN else str(index)
        rows[cpu] = {name: (values[col] if col < len(values) else 0) for name, col in _COLUMNS.items()}
    return rows


class SoftnetCollector:
    def __init__(self, proc_root: str = "/proc") -> None:
        self._path = str(Path(proc_root) / "net" / "softnet_stat")
        self._prev: Optional[Dict[str, Dict[str, int]]] = None

    def collect(self, elapsed: float) -> Dict[str, Any]:
        current = parse_softnet_stat(read_text_safe(self._path))
        previous, self._prev = self._prev, current
        total = dict.fromkeys(FIELDS, 0)
        per_cpu: Dict[str, Dict[str, int]] = {}
        for cpu, now in current.items():
            old = (previous or {}).get(cpu)
            if old is None:
                continue
            delta = {name: now[name] - old[name] if now[name] >= old[name] else now[name] for name in FIELDS}
            for name in FIELDS:
                total[name] += delta[name]
            if any(delta.values()):
                per_cpu[cpu] = delta
        return {"available": bool(current), "interval_s": float(elapsed), "total": total, "per_cpu": per_cpu}
