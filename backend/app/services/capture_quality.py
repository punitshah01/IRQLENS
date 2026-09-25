from __future__ import annotations

import math
import hashlib
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..models import CollectionSession, ExportFile


def sample_quality(
    timestamps: List[Any],
    start_time: float,
    end_time: float,
    interval: Optional[float],
    minimum_coverage: float = 0.9,
    maximum_gap_intervals: float = 2.5,
) -> Dict[str, Any]:
    valid: List[float] = []
    invalid = 0
    for value in timestamps:
        try:
            timestamp = float(value)
        except (ValueError, TypeError):
            invalid += 1
            continue
        if not math.isfinite(timestamp):
            invalid += 1
            continue
        valid.append(timestamp)
    outside = sum(timestamp < start_time or timestamp > end_time for timestamp in valid)
    in_window = [timestamp for timestamp in valid if start_time <= timestamp <= end_time]
    unique = sorted(set(in_window))
    duplicates = len(in_window) - len(unique)
    monotonic = all(current >= previous for previous, current in zip(valid, valid[1:]))
    known_interval = interval is not None and math.isfinite(interval) and interval > 0
    duration = max(0.0, end_time - start_time)
    expected = max(2, math.floor(duration / interval)) if known_interval else None
    coverage = min(100.0, 100.0 * len(unique) / expected) if expected else None
    boundaries = [start_time, *unique, end_time]
    gaps = [
        {"from_ts": previous, "to_ts": current, "seconds": current - previous}
        for previous, current in zip(boundaries, boundaries[1:])
        if current > previous
    ]
    max_gap = max((gap["seconds"] for gap in gaps), default=0.0)
    excessive_gaps = [gap for gap in gaps if known_interval and gap["seconds"] > interval * maximum_gap_intervals]
    reasons: List[str] = []
    if not known_interval:
        reasons.append("Sampling interval was not recorded.")
    if len(unique) < 2:
        reasons.append("Fewer than two samples were captured.")
    if coverage is not None and coverage < minimum_coverage * 100:
        reasons.append("Sample coverage is below the required minimum.")
    if excessive_gaps:
        reasons.append("Capture contains sampling gaps exceeding the allowed interval.")
    if invalid or outside or duplicates or not monotonic:
        reasons.append("Invalid, duplicate, out-of-window, or out-of-order timestamps were found.")
    return {
        "status": "INCOMPLETE" if reasons else "COMPLETE",
        "reasons": reasons,
        "expected_samples": expected,
        "received_samples": len(timestamps),
        "unique_samples": len(unique),
        "coverage_percent": coverage,
        "first_timestamp": unique[0] if unique else None,
        "last_timestamp": unique[-1] if unique else None,
        "max_gap_seconds": max_gap,
        "gap_count": len(excessive_gaps),
        "gaps": excessive_gaps[:100],
        "invalid_timestamps": invalid,
        "out_of_window_samples": outside,
        "duplicate_samples": duplicates,
        "monotonic": monotonic,
    }


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError):
        return {}


def build_capture_quality(session: CollectionSession, files: List[ExportFile], jobs: List[Dict[str, Any]]) -> Dict[str, Any]:
    root = Path(session.output_dir).resolve()
    config = _read_json(root / "metadata" / "capture_config.json")
    interval = config.get("sampling_interval_seconds")
    if not isinstance(interval, (int, float)) or not math.isfinite(interval) or interval <= 0:
        interval = None
    end = session.end_time if session.end_time is not None else time.time()
    checks: List[Dict[str, Any]] = []

    def add_check(name: str, complete: bool, reason: str) -> None:
        checks.append({"name": name, "status": "COMPLETE" if complete else "INCOMPLETE", "reasons": [] if complete else [reason]})

    def add_stream(name: str, rows: Any, required_field: str) -> None:
        rows = rows if isinstance(rows, list) else []
        timestamps = []
        for row in rows:
            value = row.get(required_field) if isinstance(row, dict) else None
            usable = bool(value) if isinstance(value, dict) else (
                isinstance(value, (int, float)) and math.isfinite(value) and value >= 0
            )
            timestamps.append(row.get("timestamp") if usable else None)
        check = sample_quality(timestamps, session.start_time, end, interval)
        checks.append({"name": name, **check})

    supported = {"irq", "softirq", "network", "interfaces", "system", "routes", "sockets", "ethtool"}
    add_check("categories", bool(session.categories) and set(session.categories) <= supported, "No supported capture categories were selected, or an unknown category was requested.")
    for category in session.categories:
        if category not in {"irq", "softirq", "network", "interfaces", "system"}:
            continue
        filename = "irqtop" if category == "irq" else category
        payload = _read_json(root / category / f"{filename}.json")
        field = {"irq": "rows", "softirq": "rates", "network": "samples", "interfaces": "interfaces"}.get(category)
        populated = bool(payload.get(field)) if field else bool(
            payload.get("hostname") and payload.get("os_distribution", "Unknown") != "Unknown"
            and payload.get("cpu_count", 0) and payload.get("memory_total_kb", 0)
        )
        add_check(f"{category} snapshot", populated, "Selected snapshot is missing, empty, unsupported, or invalid.")

    if "irq" in session.categories:
        payload = _read_json(root / "timeseries" / "irq" / "irq_timeseries.json")
        add_stream("IRQ samples", payload.get("samples"), "irq_rate")
    if "softirq" in session.categories:
        payload = _read_json(root / "timeseries" / "softirq" / "softirq_timeseries.json")
        add_stream("SoftIRQ samples", payload.get("samples"), "rates")
    if "system" in session.categories:
        payload = _read_json(root / "timeseries" / "cpu" / "cpu_utilization_timeseries.json")
        add_stream("CPU utilization samples", payload.get("samples"), "cpus")
    if "network" in session.categories:
        payload = _read_json(root / "timeseries" / "network" / "network_timeseries.json")
        add_stream("Network samples", payload.get("samples"), "rx_bps")
        interfaces = _read_json(root / "timeseries" / "network" / "network_interfaces_timeseries.json").get("interfaces", {})
        interfaces = interfaces if isinstance(interfaces, dict) else {}
        expected_interfaces = set(config.get("interfaces", [])) | set(interfaces)
        add_check("network interfaces", bool(expected_interfaces), "No interface stream was captured.")
        for interface in sorted(expected_interfaces):
            add_stream(f"Interface {interface}", interfaces.get(interface), "rx_bps")

    command_categories = set(session.categories) & {"network", "interfaces", "routes", "sockets", "ethtool"}
    command_results = []
    for item in files:
        path = Path(item.path).resolve()
        if item.category == "commands" and item.name == "commands.json" and root in path.parents:
            rows = _read_json(path).get("commands", [])
            if isinstance(rows, list):
                command_results.extend(row for row in rows if isinstance(row, dict))
    for category in sorted(command_categories):
        results = [row for row in command_results if row.get("category") == category]
        add_check(f"{category} command results", bool(results), "No command results have been received for this category.")
    for index, row in enumerate(command_results):
        if not row.get("success", False):
            checks.append({
                "name": str(row.get("command", f"Command {index + 1}")), "status": "FAILED",
                "reasons": [str(row.get("stderr") or "Command failed.")[:2000]],
                "exit_code": row.get("exit_code"),
            })
    for job in jobs:
        add_check(f"Remote job {job['job_id']}", job["status"] == "completed", job.get("error") or f"Remote command job is {job['status']}.")

    inventory = []
    paths = {Path(item.path).resolve() for item in files if item.category != "session"}
    paths.update([root / "summary.json", root / "metadata" / "metadata.json", root / "metadata" / "capture_config.json"])
    for path in sorted(paths):
        if root not in path.parents or path == root / "capture_quality.json":
            continue
        relative = path.relative_to(root).as_posix()
        try:
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(65536), b""):
                    digest.update(chunk)
            inventory.append({"path": relative, "size_bytes": path.stat().st_size, "sha256": digest.hexdigest()})
        except OSError:
            inventory.append({"path": relative, "missing": True})
            add_check(f"File {relative}", False, "Registered capture file is missing or unreadable.")

    issues = [check for check in checks if check["status"] != "COMPLETE"]
    status = "CAPTURING" if session.status == "running" else "INCOMPLETE" if issues else "COMPLETE"
    return {
        "schema_version": 1,
        "scope": "capture_quality",
        "session_id": session.session_id,
        "sut_id": session.sut_id or "local",
        "status": status,
        "generated_at": time.time(),
        "start_time": session.start_time,
        "end_time": session.end_time,
        "duration_seconds": max(0.0, end - session.start_time),
        "requested_duration_seconds": config.get("requested_duration_seconds"),
        "sampling_interval_seconds": interval,
        "interval_source": config.get("interval_source", "unknown"),
        "minimum_sample_coverage": 0.9,
        "maximum_gap_intervals": 2.5,
        "selected_categories": session.categories,
        "checks": checks,
        "issue_count": len(issues),
        "artifacts": inventory,
        "limitations": [
            "COMPLETE describes evidence availability, not a workload or silicon PASS verdict.",
            "Clock synchronization, counter accuracy, and IRQ-to-CPU mapping are not validated by this check.",
            "CPU frequency is optional and does not determine completeness.",
        ],
    }