from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Dict, List, Optional

from ..collectors import (
    IRQCollector, NetworkCollector, PCIeCollector, SoftIRQCollector, SoftnetCollector, StorageCollector,
    SystemCollector, collect_cpu_topology,
)
from ..config import Settings
from ..models import CPUTopologyEntry, DashboardSnapshot, InterfaceInfo, IRQSample, NetworkCorrelation, NetworkSample, SoftIRQSample, SystemInfo
from ..store import SqliteStore

PCIE_INTERVAL_SECONDS = 10.0
TOPOLOGY_INTERVAL_SECONDS = 60.0


def _read_cpu_stat_counters() -> Dict[str, tuple[int, int]]:
    counters: Dict[str, tuple[int, int]] = {}
    stat_path = Path("/proc/stat")
    if not stat_path.exists():
        return counters
    for line in stat_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.startswith("cpu") or line.startswith("cpu "):
            continue
        parts = line.split()
        cpu_label = parts[0]
        if not cpu_label.startswith("cpu") or not cpu_label[3:].isdigit():
            continue
        nums: List[int] = []
        for token in parts[1:]:
            try:
                nums.append(int(token))
            except ValueError:
                nums.append(0)
        while len(nums) < 8:
            nums.append(0)
        user, nice, system, idle, iowait, irq, softirq, steal = nums[:8]
        total = user + nice + system + idle + iowait + irq + softirq + steal
        idle_total = idle + iowait
        counters[cpu_label[3:]] = (total, idle_total)
    return counters


def _cpu_utilization_percent(
    previous: Optional[Dict[str, tuple[int, int]]],
    current: Dict[str, tuple[int, int]],
) -> Dict[str, float]:
    if not previous:
        return {}
    utilization: Dict[str, float] = {}
    for cpu, (cur_total, cur_idle) in current.items():
        prev = previous.get(cpu)
        if not prev:
            continue
        prev_total, prev_idle = prev
        total_delta = cur_total - prev_total
        idle_delta = cur_idle - prev_idle
        # Counter reset or first usable sample for this CPU: re-baseline.
        if total_delta <= 0 or idle_delta < 0:
            continue
        busy_delta = max(0, total_delta - idle_delta)
        utilization[cpu] = max(0.0, min(100.0, (busy_delta / total_delta) * 100.0))
    return utilization


class TelemetrySampler:
    def __init__(self, settings: Settings, store: SqliteStore, ws_manager: object, sut_ip: str = "local") -> None:
        self.settings = settings
        self.store = store
        self.ws_manager = ws_manager
        self.sut_ip = sut_ip

        self.irq_collector = IRQCollector()
        self.softirq_collector = SoftIRQCollector()
        self.net_collector = NetworkCollector()
        self.system_collector = SystemCollector()
        self.softnet_collector = SoftnetCollector()
        self.storage_collector = StorageCollector(include_virtual=settings.storage_include_virtual)
        self.pcie_collector = PCIeCollector()
        self._pcie_at = 0.0
        self._topology_at = 0.0

        self._task: Optional[asyncio.Task] = None
        self._stop: Optional[asyncio.Event] = None
        self._last_monotonic: Optional[float] = None
        self._last_snapshot: Optional[DashboardSnapshot] = None
        self._status = "idle"
        self._logger = logging.getLogger("irqlens.sampler")
        self._previous_cpu_stat: Optional[Dict[str, tuple[int, int]]] = None

    @property
    def status(self) -> str:
        return self._status

    @property
    def snapshot(self) -> Optional[DashboardSnapshot]:
        return self._last_snapshot

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._status = "starting"
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="irqlens-sampler")

    async def stop(self) -> None:
        if not self._task or not self._stop:
            return
        self._stop.set()
        await self._task
        self._task = None
        self._stop = None

    def _network_correlation(self, irq_rows: List[IRQSample], network_rows: List[NetworkSample], ts: float) -> List[NetworkCorrelation]:
        by_iface: Dict[str, Dict[str, float]] = {}
        for irq in irq_rows:
            iface = irq.nic
            if not iface:
                continue
            if iface not in by_iface:
                by_iface[iface] = {"rx_irqs": 0.0, "tx_irqs": 0.0, "rx_irq_rate": 0.0, "tx_irq_rate": 0.0}
            bucket = by_iface[iface]
            if irq.direction == "RX":
                bucket["rx_irqs"] += 1
                bucket["rx_irq_rate"] += irq.total_rate
            elif irq.direction == "TX":
                bucket["tx_irqs"] += 1
                bucket["tx_irq_rate"] += irq.total_rate
            elif irq.direction == "TxRx":
                bucket["rx_irqs"] += 1
                bucket["tx_irqs"] += 1
                bucket["rx_irq_rate"] += irq.total_rate / 2.0
                bucket["tx_irq_rate"] += irq.total_rate / 2.0

        net_map = {row.interface: row for row in network_rows}
        all_ifaces = sorted(set(net_map.keys()) | set(by_iface.keys()))
        out: List[NetworkCorrelation] = []
        for iface in all_ifaces:
            irq_data = by_iface.get(iface)
            net = net_map.get(iface)
            out.append(
                NetworkCorrelation(
                    timestamp=ts,
                    interface=iface,
                    rx_irqs=int((irq_data or {}).get("rx_irqs", 0)),
                    tx_irqs=int((irq_data or {}).get("tx_irqs", 0)),
                    rx_irq_rate=float((irq_data or {}).get("rx_irq_rate", 0.0)),
                    tx_irq_rate=float((irq_data or {}).get("tx_irq_rate", 0.0)),
                    rx_pps=float(net.rx_pps if net else 0.0),
                    tx_pps=float(net.tx_pps if net else 0.0),
                    rx_bps=float(net.rx_bps if net else 0.0),
                    tx_bps=float(net.tx_bps if net else 0.0),
                    correlation_available=irq_data is not None,
                )
            )
        return out

    async def _run(self) -> None:
        if self._stop is None:
            self._stop = asyncio.Event()
        stop_event = self._stop
        self._status = "running"
        while not stop_event.is_set():
            started = time.monotonic()
            ts = time.time()
            elapsed = max(1e-3, started - self._last_monotonic) if self._last_monotonic else self.settings.collection_interval
            self._last_monotonic = started

            try:
                current_cpu_stat = _read_cpu_stat_counters()
                cpu_utilization = _cpu_utilization_percent(self._previous_cpu_stat, current_cpu_stat)
                self._previous_cpu_stat = current_cpu_stat

                irq_rows = [IRQSample(**row) for row in self.irq_collector.sample_rows(self.sut_ip, ts, elapsed)]
                cpu_labels = self.irq_collector.cpu_labels_seen

                soft_totals, soft_per_cpu = self.softirq_collector.parse()
                soft_rates, soft_cpu_rates = self.softirq_collector.rates(soft_totals, soft_per_cpu, elapsed)
                soft = SoftIRQSample(
                    timestamp=ts,
                    sut_ip=self.sut_ip,
                    totals=soft_totals,
                    rates=soft_rates,
                    per_cpu_rates=soft_cpu_rates,
                )

                net_rows_raw, net_global, iface_info_raw = self.net_collector.collect(elapsed, ts)
                net_rows = [NetworkSample(sut_ip=self.sut_ip, **row) for row in net_rows_raw]
                iface_info = [InterfaceInfo.model_validate(row) for row in iface_info_raw]
                system = SystemInfo.model_validate(self.system_collector.collect(ts))
                softnet = self.softnet_collector.collect(elapsed)
                storage = self.storage_collector.collect(elapsed)
                pcie = None
                if ts - self._pcie_at >= PCIE_INTERVAL_SECONDS:
                    pcie, self._pcie_at = self.pcie_collector.collect(), ts
                topology = None
                if ts - self._topology_at >= TOPOLOGY_INTERVAL_SECONDS:
                    topology = [CPUTopologyEntry.model_validate(row) for row in collect_cpu_topology(system.cpu_model)]
                    self._topology_at = ts

                with self.store.transaction():
                    self.store.add_irq_samples(irq_rows)
                    self.store.add_softirq_sample(soft)
                    self.store.add_network_samples(net_rows)
                    self.store.add_interfaces(self.sut_ip, iface_info)
                    self.store.add_system(self.sut_ip, system)
                    if cpu_utilization:
                        self.store.add_cpu_utilization(self.sut_ip, cpu_utilization, timestamp=ts)
                    if softnet["available"]:
                        self.store.add_softnet(self.sut_ip, softnet, ts)
                    if storage:
                        self.store.add_storage(self.sut_ip, storage, ts)
                    if pcie:
                        self.store.add_pcie(self.sut_ip, pcie, ts)
                    if topology:
                        self.store.add_cpu_topology(self.sut_ip, topology, timestamp=ts)
                    self.store.prune(self.sut_ip, ts)

                highest_irq = irq_rows[0].irq_name if irq_rows else "N/A"
                highest_irq_rate = irq_rows[0].total_rate if irq_rows else 0.0
                highest_soft_source = max(soft_rates.items(), key=lambda kv: kv[1])[0] if soft_rates else "N/A"

                cpu_loads = {}
                for row in irq_rows:
                    for cpu, val in row.cpu_rates.items():
                        cpu_loads[cpu] = cpu_loads.get(cpu, 0.0) + val
                highest_cpu = max(cpu_loads.items(), key=lambda kv: kv[1])[0] if cpu_loads else "N/A"

                irq_summary = {
                    "total_irq_per_sec": sum(row.total_rate for row in irq_rows),
                    "total_softirq_per_sec": sum(soft_rates.values()),
                    "total_interrupts": sum(row.total_count for row in irq_rows),
                    "active_irq_lines": len(irq_rows),
                    "active_cpus": len(cpu_labels),
                    "highest_irq_rate": highest_irq_rate,
                    "highest_irq_source": highest_irq,
                    "highest_cpu_irq_load": highest_cpu,
                    "highest_softirq_source": highest_soft_source,
                    "network_related_irqs": len([row for row in irq_rows if row.source_class == "network"]),
                    "irqs_with_affinity": len([row for row in irq_rows if row.affinity_list and row.affinity_list != "N/A"]),
                }

                correlation = self._network_correlation(irq_rows, net_rows, ts)
                self._last_snapshot = DashboardSnapshot(
                    timestamp=ts,
                    system=system,
                    irq_summary=irq_summary,
                    irq_rows=irq_rows[:256],
                    softirq=soft,
                    network_global=net_global,
                    interfaces=iface_info,
                    network_samples=net_rows,
                    network_correlation=correlation,
                )

                await self.ws_manager.broadcast(
                    {
                        "type": "telemetry",
                        "timestamp": ts,
                        "host": self.sut_ip,
                        "sut_id": self.sut_ip,
                        "cpu_utilization": cpu_utilization,
                        "irq_summary": irq_summary,
                        "network_global": net_global,
                    }
                )
            except Exception as exc:
                self._status = "failed"
                self._logger.exception("telemetry sampling failed: %s", exc)
                await self.ws_manager.broadcast({"type": "collector_error", "message": str(exc), "timestamp": ts})

            delay = max(0.01, self.settings.collection_interval - (time.monotonic() - started))
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

        self._status = "stopped"
