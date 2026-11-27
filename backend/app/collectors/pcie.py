from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import link_name, list_msi_irqs, read_first_line, read_int, read_text_safe

AER_FILES = ("aer_dev_correctable", "aer_dev_nonfatal", "aer_dev_fatal")
# Longest prefix first: 0x0108 (NVMe) must win over 0x01 (mass storage).
_KINDS = (
    ("0108", "NVMe"),
    ("0b40", "Accelerator"),
    ("01", "Storage"),
    ("02", "NIC"),
    ("03", "GPU"),
    ("12", "Accelerator"),
)


def classify_pci_class(class_code: str) -> str:
    digits = class_code.strip().lower().replace("0x", "").zfill(6)
    for prefix, kind in _KINDS:
        if digits.startswith(prefix):
            return kind
    return ""


def parse_link_speed(text: str) -> float:
    match = re.search(r"([\d.]+)\s*GT/s", text or "")
    return float(match.group(1)) if match else 0.0


def parse_aer_total(text: str) -> Optional[int]:
    total = 0
    seen = False
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) != 2 or not parts[1].isdigit():
            continue
        if parts[0].startswith("TOTAL_"):
            return int(parts[1])
        total += int(parts[1])
        seen = True
    return total if seen else None


def link_degraded(device: Dict[str, Any]) -> bool:
    current_speed = parse_link_speed(str(device.get("current_link_speed", "")))
    max_speed = parse_link_speed(str(device.get("max_link_speed", "")))
    current_width = int(device.get("current_link_width") or 0)
    max_width = int(device.get("max_link_width") or 0)
    slower = current_speed > 0 and max_speed > 0 and current_speed < max_speed
    narrower = current_width > 0 and max_width > 0 and current_width < max_width
    return slower or narrower


class PCIeCollector:
    def __init__(self, sys_root: str = "/sys") -> None:
        self._devices = Path(sys_root) / "bus" / "pci" / "devices"
        self._prev_aer: Dict[str, int] = {}

    def collect(self) -> List[Dict[str, Any]]:
        try:
            entries = sorted(self._devices.iterdir(), key=lambda item: item.name)
        except OSError:
            return []
        rows: List[Dict[str, Any]] = []
        aer_totals: Dict[str, int] = {}
        for entry in entries:
            class_code = read_first_line(str(entry / "class"), "")
            kind = classify_pci_class(class_code) if class_code else ""
            if not kind:
                continue
            numa_node = read_int(entry / "numa_node")
            msi_irqs = list_msi_irqs(entry)
            try:
                interfaces = sorted(child.name for child in (entry / "net").iterdir())
            except OSError:
                interfaces = []
            row: Dict[str, Any] = {
                "bdf": entry.name,
                "kind": kind,
                "vendor": read_first_line(str(entry / "vendor"), "N/A"),
                "device": read_first_line(str(entry / "device"), "N/A"),
                "class": class_code,
                "driver": link_name(entry / "driver"),
                "interfaces": interfaces,
                "numa_node": numa_node if numa_node is not None and numa_node >= 0 else None,
                "current_link_speed": read_first_line(str(entry / "current_link_speed"), "N/A"),
                "current_link_width": read_int(entry / "current_link_width", 0),
                "max_link_speed": read_first_line(str(entry / "max_link_speed"), "N/A"),
                "max_link_width": read_int(entry / "max_link_width", 0),
                "msi_irqs": msi_irqs,
                "msi_irq_count": len(msi_irqs),
            }
            for name in AER_FILES:
                row[name] = parse_aer_total(read_text_safe(str(entry / name)))
            aer_total = sum(int(row[name] or 0) for name in AER_FILES)
            aer_totals[entry.name] = aer_total
            row["aer_total"] = aer_total
            row["aer_increasing"] = entry.name in self._prev_aer and aer_total > self._prev_aer[entry.name]
            row["link_degraded"] = link_degraded(row)
            rows.append(row)
        self._prev_aer = aer_totals
        return rows
