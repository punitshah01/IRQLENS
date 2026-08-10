from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .base import read_text_safe


@dataclass
class IRQRow:
    irq: str
    irq_name: str
    counts: List[int]


class IRQCollector:
    def __init__(self) -> None:
        self._prev_counts: Dict[str, List[int]] = {}
        self._prev_total: Dict[str, int] = {}

    def cpu_labels(self) -> List[str]:
        lines = read_text_safe("/proc/interrupts").splitlines()
        if not lines:
            return []
        return [x for x in lines[0].split() if x.startswith("CPU")]

    def parse(self) -> Tuple[List[str], Dict[str, IRQRow]]:
        lines = read_text_safe("/proc/interrupts").splitlines()
        if not lines:
            return [], {}
        cpu_labels = [x for x in lines[0].split() if x.startswith("CPU")]
        rows: Dict[str, IRQRow] = {}
        for line in lines[1:]:
            if ":" not in line:
                continue
            left, right = line.split(":", 1)
            irq = left.strip()
            tokens = right.split()
            if len(tokens) < len(cpu_labels) + 1:
                continue
            counts: List[int] = []
            valid = True
            for idx in range(len(cpu_labels)):
                try:
                    counts.append(int(tokens[idx]))
                except ValueError:
                    valid = False
                    break
            if not valid:
                continue
            irq_name = " ".join(tokens[len(cpu_labels):]).strip() or "N/A"
            rows[irq] = IRQRow(irq=irq, irq_name=irq_name, counts=counts)
        return cpu_labels, rows

    def affinity_for_irq(self, irq: str) -> str:
        path = Path(f"/proc/irq/{irq}/smp_affinity_list")
        if not path.exists():
            return "N/A"
        try:
            text = path.read_text(encoding="utf-8", errors="ignore").strip()
        except Exception:
            return "N/A"
        return text or "N/A"

    def numa_for_irq(self, irq: str) -> str:
        path = Path(f"/sys/kernel/irq/{irq}/node")
        if not path.exists():
            return "N/A"
        try:
            return path.read_text(encoding="utf-8", errors="ignore").strip() or "N/A"
        except Exception:
            return "N/A"

    def classify(self, irq_name: str) -> Tuple[str, str]:
        lower = irq_name.lower()
        if any(tok in lower for tok in ["eth", "ens", "eno", "enp", "mlx", "virtio", "bnxt", "ixgbe", "i40e", "ice", "veth", "bond", "br"]):
            return "network", "MSI/MSI-X"
        return "other", "N/A"

    def rates(self, rows: Dict[str, IRQRow], elapsed: float) -> Dict[str, Dict[str, float]]:
        out: Dict[str, Dict[str, float]] = {}
        for irq, row in rows.items():
            prev = self._prev_counts.get(irq)
            if prev is None or len(prev) != len(row.counts):
                continue
            cpu_rates: Dict[str, float] = {}
            total_delta = 0
            for idx, value in enumerate(row.counts):
                delta = value - prev[idx]
                if delta < 0:
                    delta = value
                total_delta += delta
                if delta > 0:
                    cpu_rates[str(idx)] = float(delta) / elapsed
            total_count = sum(row.counts)
            out[irq] = {
                "total_rate": float(total_delta) / elapsed,
                "total_count": float(total_count),
                "cpu_rates": cpu_rates,
            }
        self._prev_counts = {k: v.counts[:] for k, v in rows.items()}
        self._prev_total = {k: sum(v.counts) for k, v in rows.items()}
        return out

    def highest(self, rates: Dict[str, Dict[str, float]]) -> Tuple[str, float]:
        if not rates:
            return "N/A", 0.0
        top_irq = max(rates.items(), key=lambda item: float(item[1].get("total_rate", 0.0)))
        return top_irq[0], float(top_irq[1].get("total_rate", 0.0))
