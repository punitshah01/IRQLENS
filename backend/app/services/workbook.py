from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from ..collectors.softnet import FIELDS as SOFTNET_FIELDS
from ..collectors.storage import METRICS as STORAGE_METRICS
from ..models import CollectionSession
from ..store import SqliteStore
from .exporter import utc_iso
from .visualization import flatten_irq_row

SHEET_NAMES = ("Summary", "IRQ_Top", "SoftIRQ", "Network", "Softnet", "Storage", "PCIe_Cards", "TimeSeries_Metrics")
IRQ_TOP_LIMIT = 200

_IRQ_HEADERS = [
    "irq", "irq_name", "nic", "source_class", "direction", "total_irq_s", "peak_irq_s", "top_cpu", "top_cpu_irq_s",
    "numa_node", "handler_numa_nodes", "cross_numa_flag", "active_cpu_count", "affinity_list",
]
_NETWORK_KEYS = ["rx_bps", "tx_bps", "rx_pps", "tx_pps", "rx_err_ps", "tx_err_ps", "rx_drop_ps", "tx_drop_ps"]
_PCIE_HEADERS = [
    "bdf", "kind", "vendor", "device", "class", "driver", "interfaces", "numa_node",
    "current_link_speed", "max_link_speed", "current_link_width", "max_link_width", "link_degraded",
    "msi_irq_count", "aer_dev_correctable", "aer_dev_nonfatal", "aer_dev_fatal",
]
_STORAGE_METADATA = ["model", "scheduler", "nr_requests", "rotational", "numa_node", "msi_irq_count"]
_TIMESERIES_METRICS = [
    "irq_total_s", "softirq_total_s", "rx_bytes_s", "tx_bytes_s", "rx_pps", "tx_pps", "rx_drop_ps", "tx_drop_ps",
    "cpu_util_avg_pct", "cpu_util_max_pct", "storage_read_iops", "storage_write_iops",
    "storage_read_mb_s", "storage_write_mb_s", "softnet_dropped", "softnet_time_squeeze",
]


def _avg(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _peak(values: List[float]) -> float:
    return max(values) if values else 0.0


def build_sheets(store: SqliteStore, session: CollectionSession, interval: Optional[float]) -> List[Dict[str, Any]]:
    sut_id = session.sut_id or "local"
    start = float(session.start_time)
    end = float(session.end_time if session.end_time is not None else time.time())
    cpu_to_node = {
        str(item.cpu_id): item.numa_node for item in store.latest_cpu_topology(sut_id) if item.numa_node is not None
    }
    timeline: Dict[float, Dict[str, float]] = {}

    def put(timestamp: float, **values: float) -> None:
        timeline.setdefault(round(float(timestamp), 3), {}).update(values)

    irq_flat = [flatten_irq_row(row, cpu_to_node) for row in store.irq_capture_summary(sut_id, start, end)]
    irq_series = store.irq_rate_series(sut_id, start, end)
    for point in irq_series:
        put(point["timestamp"], irq_total_s=point["irq_rate"])

    softirq_classes: Dict[str, List[float]] = {}
    for sample in store.softirq_series(sut_id, start, end):
        put(sample["timestamp"], softirq_total_s=float(sum(sample["rates"].values())))
        for name, rate in sample["rates"].items():
            softirq_classes.setdefault(name, []).append(float(rate))
    softirq_rows = [[name, _avg(rates), _peak(rates), rates[-1]] for name, rates in sorted(softirq_classes.items())]

    network_series = store.network_rate_series(sut_id, start, end)
    for point in network_series:
        put(
            point["timestamp"], rx_bytes_s=point["rx_bps"], tx_bytes_s=point["tx_bps"], rx_pps=point["rx_pps"],
            tx_pps=point["tx_pps"], rx_drop_ps=point["rx_drop_ps"], tx_drop_ps=point["tx_drop_ps"],
        )
    network_rows = []
    for interface, points in sorted(store.network_interface_series(sut_id, start, end).items()):
        columns = {key: [float(point[key]) for point in points] for key in _NETWORK_KEYS}
        network_rows.append([
            interface, _avg(columns["rx_bps"]), _peak(columns["rx_bps"]), _avg(columns["tx_bps"]), _peak(columns["tx_bps"]),
            _avg(columns["rx_pps"]), _avg(columns["tx_pps"]), _avg(columns["rx_err_ps"]), _avg(columns["tx_err_ps"]),
            _avg(columns["rx_drop_ps"]), _avg(columns["tx_drop_ps"]),
        ])

    for point in store.cpu_utilization_series(sut_id, start, end):
        put(point["timestamp"], cpu_util_avg_pct=point["avg"], cpu_util_max_pct=point["max"])

    softnet_total = dict.fromkeys(SOFTNET_FIELDS, 0)
    softnet_cpus: Dict[str, Dict[str, int]] = {}
    for sample in store.json_series("softnet_samples", sut_id, start, end):
        data = sample["data"] if isinstance(sample["data"], dict) else {}
        total = data.get("total", {})
        put(sample["timestamp"], softnet_dropped=float(total.get("dropped") or 0), softnet_time_squeeze=float(total.get("time_squeeze") or 0))
        for name in SOFTNET_FIELDS:
            softnet_total[name] += int(total.get(name) or 0)
        for cpu, delta in (data.get("per_cpu") or {}).items():
            bucket = softnet_cpus.setdefault(str(cpu), dict.fromkeys(SOFTNET_FIELDS, 0))
            for name in SOFTNET_FIELDS:
                bucket[name] += int(delta.get(name) or 0)
    softnet_rows = [["ALL"] + [softnet_total[name] for name in SOFTNET_FIELDS]]
    for cpu in sorted(softnet_cpus, key=lambda value: int(value) if value.isdigit() else 0):
        softnet_rows.append([cpu] + [softnet_cpus[cpu][name] for name in SOFTNET_FIELDS])

    storage_series = store.storage_series(sut_id, start, end)
    storage_metadata = {str(item.get("device")): item for item in store.latest_json("storage_samples", sut_id, [])}
    storage_rows = []
    storage_iops: List[float] = []
    for device, points in sorted(storage_series.items()):
        columns = {key: [point[key] for point in points] for key in STORAGE_METRICS}
        # Ticks without I/O report a zero await and would dilute the average.
        read_await = [value for value in columns["avg_read_await_ms"] if value > 0.0]
        write_await = [value for value in columns["avg_write_await_ms"] if value > 0.0]
        storage_iops.append(_avg(columns["read_iops"]) + _avg(columns["write_iops"]))
        metadata = storage_metadata.get(device, {})
        storage_rows.append([
            device, _avg(columns["read_iops"]), _peak(columns["read_iops"]),
            _avg(columns["write_iops"]), _peak(columns["write_iops"]),
            _avg(columns["read_mb_s"]), _peak(columns["read_mb_s"]),
            _avg(columns["write_mb_s"]), _peak(columns["write_mb_s"]),
            _avg(read_await), _peak(read_await), _avg(write_await), _peak(write_await),
            _peak(columns["queue_depth_in_flight"]), _avg(columns["util_pct"]),
        ] + [metadata.get(key) for key in _STORAGE_METADATA])
    for sample in store.json_series("storage_samples", sut_id, start, end):
        devices = sample["data"] if isinstance(sample["data"], list) else []
        put(
            sample["timestamp"],
            storage_read_iops=sum(float(item.get("read_iops") or 0.0) for item in devices),
            storage_write_iops=sum(float(item.get("write_iops") or 0.0) for item in devices),
            storage_read_mb_s=sum(float(item.get("read_mb_s") or 0.0) for item in devices),
            storage_write_mb_s=sum(float(item.get("write_mb_s") or 0.0) for item in devices),
        )

    pcie = store.latest_json("pcie_samples", sut_id, [])
    pcie_rows = [[device.get(key) for key in _PCIE_HEADERS] for device in pcie]

    system = store.latest_system(sut_id)
    irq_rates = [float(point["irq_rate"]) for point in irq_series]
    summary_rows = [
        ["session_name", session.session_id],
        ["sut_id", sut_id],
        ["hostname", session.hostname],
        ["status", session.status],
        ["start_utc", utc_iso(start)],
        ["end_utc", utc_iso(end)],
        ["duration_s", max(0.0, end - start)],
        ["sampling_interval_s", interval],
        ["categories", ", ".join(session.categories)],
        ["os_distribution", session.os_distribution],
        ["kernel", session.kernel],
        ["cpu_count", system.cpu_count if system else None],
        ["irqbalance", system.irqbalance if system else "unknown"],
        ["samples", len(timeline)],
        ["avg_irq_s", _avg(irq_rates)],
        ["peak_irq_s", _peak(irq_rates)],
        ["cross_numa_irqs", sum(1 for row in irq_flat if row["cross_numa_flag"])],
        ["avg_rx_bytes_s", _avg([float(point["rx_bps"]) for point in network_series])],
        ["avg_tx_bytes_s", _avg([float(point["tx_bps"]) for point in network_series])],
        ["softnet_dropped", softnet_total["dropped"]],
        ["softnet_time_squeeze", softnet_total["time_squeeze"]],
        ["storage_devices", len(storage_rows)],
        ["avg_storage_iops", sum(storage_iops)],
        ["pcie_devices", len(pcie)],
        ["pcie_link_warnings", sum(1 for device in pcie if device.get("link_degraded"))],
        ["pcie_aer_errors", sum(int(device.get("aer_total") or 0) for device in pcie)],
    ]

    timeseries_rows = [
        [utc_iso(timestamp), timestamp] + [values.get(key) for key in _TIMESERIES_METRICS]
        for timestamp, values in sorted(timeline.items())
    ]

    return [
        {"name": "Summary", "headers": ["metric", "value"], "rows": summary_rows},
        {"name": "IRQ_Top", "headers": _IRQ_HEADERS, "rows": [[row[key] for key in _IRQ_HEADERS] for row in irq_flat[:IRQ_TOP_LIMIT]]},
        {"name": "SoftIRQ", "headers": ["class", "avg_rate_s", "peak_rate_s", "latest_rate_s"], "rows": softirq_rows},
        {"name": "Network", "headers": [
            "interface", "avg_rx_bytes_s", "peak_rx_bytes_s", "avg_tx_bytes_s", "peak_tx_bytes_s", "avg_rx_pps", "avg_tx_pps",
            "avg_rx_err_ps", "avg_tx_err_ps", "avg_rx_drop_ps", "avg_tx_drop_ps",
        ], "rows": network_rows},
        {"name": "Softnet", "headers": ["cpu"] + list(SOFTNET_FIELDS), "rows": softnet_rows},
        {"name": "Storage", "headers": [
            "device", "avg_read_iops", "peak_read_iops", "avg_write_iops", "peak_write_iops",
            "avg_read_mb_s", "peak_read_mb_s", "avg_write_mb_s", "peak_write_mb_s",
            "avg_read_await_ms", "max_read_await_ms", "avg_write_await_ms", "max_write_await_ms",
            "max_queue_depth_in_flight", "avg_util_pct",
        ] + _STORAGE_METADATA, "rows": storage_rows},
        {"name": "PCIe_Cards", "headers": _PCIE_HEADERS, "rows": pcie_rows},
        {"name": "TimeSeries_Metrics", "headers": ["timestamp_utc", "epoch_s"] + _TIMESERIES_METRICS, "rows": timeseries_rows},
    ]
