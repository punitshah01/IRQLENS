from __future__ import annotations

from typing import Dict, List, Tuple

from .base import read_text_safe


class SoftIRQCollector:
    def __init__(self) -> None:
        self._prev_totals: Dict[str, int] = {}
        self._prev_per_cpu: Dict[str, int] = {}

    def parse(self) -> Tuple[Dict[str, int], Dict[str, int]]:
        lines = read_text_safe("/proc/softirqs").splitlines()
        if not lines:
            return {}, {}
        header = [x for x in lines[0].split() if x.startswith("CPU")]
        per_cpu_totals = [0 for _ in header]
        totals: Dict[str, int] = {}

        for line in lines[1:]:
            if ":" not in line:
                continue
            name, right = line.split(":", 1)
            values = right.split()
            total = 0
            for idx, token in enumerate(values):
                try:
                    val = int(token)
                except ValueError:
                    continue
                total += val
                if idx < len(per_cpu_totals):
                    per_cpu_totals[idx] += val
            totals[name.strip()] = total

        per_cpu: Dict[str, int] = {str(i): val for i, val in enumerate(per_cpu_totals)}
        return totals, per_cpu

    def rates(self, totals: Dict[str, int], per_cpu: Dict[str, int], elapsed: float) -> Tuple[Dict[str, float], Dict[str, float]]:
        rate_totals: Dict[str, float] = {}
        for name, value in totals.items():
            old = self._prev_totals.get(name)
            if old is None:
                continue
            delta = value - old
            if delta < 0:
                delta = value
            rate_totals[name] = float(delta) / elapsed

        rate_per_cpu: Dict[str, float] = {}
        for cpu, value in per_cpu.items():
            old = self._prev_per_cpu.get(cpu)
            if old is None:
                continue
            delta = value - old
            if delta < 0:
                delta = value
            rate_per_cpu[cpu] = float(delta) / elapsed

        self._prev_totals = totals.copy()
        self._prev_per_cpu = per_cpu.copy()
        return rate_totals, rate_per_cpu

    def highest_source(self, rates: Dict[str, float]) -> Tuple[str, float]:
        if not rates:
            return "N/A", 0.0
        name, value = max(rates.items(), key=lambda item: item[1])
        return name, value
