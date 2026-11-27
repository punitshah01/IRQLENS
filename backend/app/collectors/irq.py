from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .base import link_name, list_msi_irqs, read_int, read_text_safe

_DEVICE_MAP_TTL = 30.0


@dataclass
class IRQRow:
    irq: str
    irq_name: str
    counts: List[int]


def _numa_text(value: Optional[int]) -> str:
    return str(value) if value is not None and value >= 0 else "N/A"


def build_irq_device_map(sys_root: str = "/sys") -> Tuple[Dict[str, Dict[str, str]], List[str], List[str]]:
    # Returns (irq -> owning device, all interfaces, interfaces whose IRQs sysfs lists).
    root = Path(sys_root)
    mapping: Dict[str, Dict[str, str]] = {}
    interfaces: List[str] = []
    mapped: List[str] = []
    try:
        net_entries = sorted((root / "class" / "net").iterdir(), key=lambda item: item.name)
    except OSError:
        net_entries = []
    for iface in net_entries:
        interfaces.append(iface.name)
        device = iface / "device"
        # A virtio netdev hangs off a virtio node; its MSI-X vectors belong to the PCI parent.
        if not list_msi_irqs(device) and link_name(device / "subsystem") == "virtio":
            device = device / ".."
        irqs = list_msi_irqs(device)
        if not irqs:
            continue
        mapped.append(iface.name)
        numa = _numa_text(read_int(device / "numa_node"))
        for irq in irqs:
            mapping.setdefault(irq, {"nic": iface.name, "numa_node": numa, "source_class": "network"})
    try:
        nvme_entries = sorted((root / "class" / "nvme").iterdir(), key=lambda item: item.name)
    except OSError:
        nvme_entries = []
    for controller in nvme_entries:
        device = controller / "device"
        numa = _numa_text(read_int(device / "numa_node"))
        for irq in list_msi_irqs(device):
            mapping.setdefault(irq, {"nic": "", "numa_node": numa, "source_class": "storage"})
    return mapping, interfaces, mapped


def _interface_from_label(label: str, interfaces: List[str]) -> str:
    tokens = set(re.split(r"[-@:,\s]+", label))
    for name in sorted(interfaces, key=len, reverse=True):
        if label == name or label.startswith(name + "-") or name in tokens:
            return name
    return ""


def _online_cpu_ids(sys_root: Path) -> List[str]:
    # "0-3,8" -> ["0", "1", "2", "3", "8"]
    ids: List[str] = []
    for part in read_text_safe(str(sys_root / "devices" / "system" / "cpu" / "online")).strip().split(","):
        first, _, last = part.partition("-")
        if first.isdigit() and (last.isdigit() or not last):
            ids.extend(str(cpu) for cpu in range(int(first), int(last or first) + 1))
    return ids


def _labels_without_header(first_line: str, sys_root: Path) -> List[str]:
    columns = 0
    for token in first_line.partition(":")[2].split():
        if not token.isdigit():
            break
        columns += 1
    online = _online_cpu_ids(sys_root)
    # The kernel prints one count per online CPU; without that list the columns are numbered by position.
    cpu_ids = online if 0 < len(online) <= columns else [str(cpu) for cpu in range(columns)]
    return ["CPU" + cpu for cpu in cpu_ids]


class IRQCollector:
    def __init__(self, proc_root: str = "/proc", sys_root: str = "/sys") -> None:
        self._proc = Path(proc_root)
        self._sys = Path(sys_root)
        self._interrupts = str(self._proc / "interrupts")
        self._prev_counts: Dict[str, List[int]] = {}
        self._prev_total: Dict[str, int] = {}
        self._device_map: Optional[Tuple[Dict[str, Dict[str, str]], List[str], List[str]]] = None
        self._device_map_at = 0.0
        self._cpu_ids: List[str] = []
        self.cpu_labels_seen: List[str] = []

    def cpu_labels(self) -> List[str]:
        lines = read_text_safe(self._interrupts).splitlines()
        if not lines:
            return []
        return [x for x in lines[0].split() if x.startswith("CPU")]

    def parse(self) -> Tuple[List[str], Dict[str, IRQRow]]:
        lines = read_text_safe(self._interrupts).splitlines()
        if not lines:
            return [], {}
        cpu_labels = [x for x in lines[0].split() if x.startswith("CPU")]
        body = lines[1:]
        if not cpu_labels:
            # Some kernels print no "CPU0 CPU1 ..." line (seen on 7.2 with 256 CPUs): every line is data.
            cpu_labels, body = _labels_without_header(lines[0], self._sys), lines
        if not cpu_labels:
            return [], {}
        self._cpu_ids = [label[3:] for label in cpu_labels]
        rows: Dict[str, IRQRow] = {}
        for line in body:
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
        path = self._proc / "irq" / irq / "smp_affinity_list"
        if not path.exists():
            return "N/A"
        try:
            text = path.read_text(encoding="utf-8", errors="ignore").strip()
        except Exception:
            return "N/A"
        return text or "N/A"

    def numa_for_irq(self, irq: str) -> str:
        path = self._sys / "kernel" / "irq" / irq / "node"
        if not path.exists():
            return "N/A"
        try:
            return path.read_text(encoding="utf-8", errors="ignore").strip() or "N/A"
        except Exception:
            return "N/A"

    def classify(self, irq_name: str) -> Tuple[str, str]:
        lower = irq_name.lower()
        if any(tok in lower for tok in ["nvme", "ahci", "megasas", "mpt3sas", "virtio-blk", "scsi"]):
            return "storage", "MSI/MSI-X"
        if any(tok in lower for tok in ["eth", "ens", "eno", "enp", "mlx", "virtio", "bnxt", "ixgbe", "i40e", "ice", "veth", "bond", "br"]):
            return "network", "MSI/MSI-X"
        return "other", "N/A"

    def device_map(self) -> Tuple[Dict[str, Dict[str, str]], List[str], List[str]]:
        now = time.monotonic()
        if self._device_map is None or now - self._device_map_at >= _DEVICE_MAP_TTL:
            self._device_map = build_irq_device_map(str(self._sys))
            self._device_map_at = now
        return self._device_map

    def sample_rows(self, sut_id: str, ts: float, elapsed: float) -> List[Dict[str, object]]:
        self.cpu_labels_seen, parsed = self.parse()
        rates = self.rates(parsed, elapsed)
        mapping, interfaces, mapped_interfaces = self.device_map()
        measured = [(irq, row, rates[irq]) for irq, row in parsed.items() if irq in rates]
        # Idle lines are dropped; one row is kept so every tick stays visible in history.
        active = [item for item in measured if float(item[2]["total_rate"]) > 0.0] or measured[:1]
        rows: List[Dict[str, object]] = []
        for irq, row, info in active:
            tokens = row.irq_name.split()
            label = tokens[-1] if tokens else "N/A"
            source_class, irq_type = self.classify(row.irq_name)
            device = mapping.get(irq)
            nic = ""
            if device:
                source_class, nic = device["source_class"], device["nic"]
            elif interfaces:
                # Label matching is only trusted for interfaces sysfs cannot describe.
                guess = _interface_from_label(label, interfaces)
                if guess and guess not in mapped_interfaces:
                    source_class, nic = "network", guess
            elif source_class == "network":
                nic = label.split("-")[0]
            numa_node = self.numa_for_irq(irq)
            if device and numa_node in ("N/A", "-1"):
                numa_node = device["numa_node"]
            lowered = label.lower()
            if "txrx" in lowered or ("rx" in lowered and "tx" in lowered):
                direction = "TxRx"
            else:
                direction = "RX" if "rx" in lowered else "TX" if "tx" in lowered else "Other"
            queue = re.search(r"(\d+)$", label)
            rows.append({
                "timestamp": ts,
                "sut_ip": sut_id,
                "sut_id": sut_id,
                "irq": irq,
                "irq_name": row.irq_name,
                "device": label,
                "interrupt_type": irq_type,
                "affinity_list": self.affinity_for_irq(irq),
                "numa_node": numa_node,
                "nic": nic,
                "queue": queue.group(1) if queue and source_class == "network" else "",
                "direction": direction,
                "source_class": source_class,
                "total_count": int(info.get("total_count", 0)),
                "total_rate": float(info.get("total_rate", 0.0)),
                "cpu_rates": dict(info.get("cpu_rates", {})),
            })
        rows.sort(key=lambda item: item["total_rate"], reverse=True)
        return rows

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
                    # The header names the CPU; column position differs when CPUs are offline.
                    cpu = self._cpu_ids[idx] if idx < len(self._cpu_ids) else str(idx)
                    cpu_rates[cpu] = float(delta) / elapsed
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
