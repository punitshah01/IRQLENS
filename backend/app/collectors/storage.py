from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .base import list_msi_irqs, read_first_line, read_int, read_text_safe

METRICS = (
    "read_iops", "write_iops", "read_mb_s", "write_mb_s",
    "avg_read_await_ms", "avg_write_await_ms", "queue_depth_in_flight", "util_pct",
)
_PHYSICAL = re.compile(r"^(nvme\d+n\d+|sd[a-z]+|vd[a-z]+|xvd[a-z]+|hd[a-z]+|mmcblk\d+)$")
_VIRTUAL = re.compile(r"^(loop\d+|ram\d+|dm-\d+|md\d+|zram\d+)$")
_NVME_NAMESPACE = re.compile(r"^(nvme\d+)n\d+$")
_SECTOR_BYTES = 512  # /proc/diskstats always counts 512-byte sectors
_MIB = 1024 * 1024
_METADATA_TTL = 30.0


def parse_diskstats(text: str, include_virtual: bool = False) -> Dict[str, Dict[str, int]]:
    devices: Dict[str, Dict[str, int]] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 14:
            continue
        name = parts[2]
        if not (_PHYSICAL.match(name) or (include_virtual and _VIRTUAL.match(name))):
            continue
        try:
            n = [int(value) for value in parts[3:14]]
        except ValueError:
            continue
        devices[name] = {
            "reads": n[0], "sectors_read": n[2], "read_ms": n[3],
            "writes": n[4], "sectors_written": n[6], "write_ms": n[7],
            "in_flight": n[8], "io_ms": n[9],
        }
    return devices


def storage_rates(previous: Optional[Dict[str, int]], current: Dict[str, int], elapsed: float) -> Dict[str, float]:
    rates = dict.fromkeys(METRICS, 0.0)
    rates["queue_depth_in_flight"] = float(current["in_flight"])
    if not previous or elapsed <= 0:
        return rates

    def delta(key: str) -> int:
        value = current[key] - previous[key]
        return value if value >= 0 else current[key]

    reads, writes = delta("reads"), delta("writes")
    rates.update({
        "read_iops": reads / elapsed,
        "write_iops": writes / elapsed,
        "read_mb_s": delta("sectors_read") * _SECTOR_BYTES / _MIB / elapsed,
        "write_mb_s": delta("sectors_written") * _SECTOR_BYTES / _MIB / elapsed,
        "avg_read_await_ms": delta("read_ms") / reads if reads else 0.0,
        "avg_write_await_ms": delta("write_ms") / writes if writes else 0.0,
        "util_pct": max(0.0, min(100.0, delta("io_ms") / (elapsed * 1000.0) * 100.0)),
    })
    return rates


class StorageCollector:
    def __init__(self, proc_root: str = "/proc", sys_root: str = "/sys", include_virtual: bool = False) -> None:
        self._diskstats = str(Path(proc_root) / "diskstats")
        self._sys = Path(sys_root)
        self._include_virtual = include_virtual
        self._prev: Dict[str, Dict[str, int]] = {}
        self._metadata: Dict[str, Tuple[float, Dict[str, Any]]] = {}

    def device_metadata(self, device: str) -> Dict[str, Any]:
        cached = self._metadata.get(device)
        now = time.monotonic()
        if cached and now - cached[0] < _METADATA_TTL:
            return cached[1]
        block = self._sys / "block" / device
        scheduler_raw = read_first_line(str(block / "queue" / "scheduler"), "")
        active = re.search(r"\[([^\]]+)\]", scheduler_raw)
        rotational = read_int(block / "queue" / "rotational")
        numa_node = None
        for candidate in (block / "device" / "numa_node", block / "device" / "device" / "numa_node"):
            value = read_int(candidate)
            if value is not None and value >= 0:
                numa_node = value
                break
        namespace = _NVME_NAMESPACE.match(device)
        msi_irqs = list_msi_irqs(self._sys / "class" / "nvme" / namespace.group(1) / "device") if namespace else []
        metadata = {
            "model": read_first_line(str(block / "device" / "model"), ""),
            "scheduler": active.group(1) if active else (scheduler_raw or "N/A"),
            "nr_requests": read_int(block / "queue" / "nr_requests"),
            "rotational": None if rotational is None else bool(rotational),
            "numa_node": numa_node,
            "msi_irqs": msi_irqs,
            "msi_irq_count": len(msi_irqs),
        }
        self._metadata[device] = (now, metadata)
        return metadata

    def collect(self, elapsed: float) -> List[Dict[str, Any]]:
        current = parse_diskstats(read_text_safe(self._diskstats), self._include_virtual)
        rows: List[Dict[str, Any]] = []
        for device in sorted(current):
            rates = storage_rates(self._prev.get(device), current[device], elapsed)
            rows.append({"device": device, **rates, **self.device_metadata(device)})
        self._prev = current
        return rows
