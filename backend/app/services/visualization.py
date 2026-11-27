from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Tuple, Union

CROSS_NUMA_SHARE = 0.10


def _node(value: Any) -> Optional[int]:
    try:
        node = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return node if node >= 0 else None


def cross_numa_mismatch(
    irq_numa_node: Any,
    cpu_rates: Mapping[str, float],
    cpu_to_node: Mapping[str, int],
    threshold: float = CROSS_NUMA_SHARE,
) -> Dict[str, Any]:
    device_node = _node(irq_numa_node)
    total = sum(max(0.0, float(rate)) for rate in cpu_rates.values())
    handler_nodes = set()
    remote_share = 0.0
    if total > 0:
        for cpu, rate in cpu_rates.items():
            share = max(0.0, float(rate)) / total
            node = _node(cpu_to_node.get(str(cpu)))
            if share <= threshold or node is None:
                continue
            handler_nodes.add(node)
            if device_node is not None and node != device_node:
                remote_share += share
    return {
        "cross_numa_flag": remote_share > 0.0,
        "handler_numa_nodes": sorted(handler_nodes),
        "remote_share_pct": round(remote_share * 100.0, 1),
    }


def flatten_irq_row(row: Mapping[str, Any], cpu_to_node: Mapping[str, int]) -> Dict[str, Any]:
    cpu_rates = {str(cpu): float(rate) for cpu, rate in (row.get("cpu_rates") or {}).items()}
    top_cpu, top_rate = max(cpu_rates.items(), key=lambda item: item[1], default=("", 0.0))
    numa = cross_numa_mismatch(row.get("numa_node"), cpu_rates, cpu_to_node)
    return {
        "irq": str(row.get("irq", "")),
        "irq_name": str(row.get("irq_name", "")),
        "nic": str(row.get("nic", "") or ""),
        "source_class": str(row.get("source_class", "") or ""),
        "direction": str(row.get("direction", "") or ""),
        "total_irq_s": float(row.get("total_rate") or 0.0),
        "peak_irq_s": float(row.get("peak_rate") or row.get("total_rate") or 0.0),
        "top_cpu": top_cpu,
        "top_cpu_irq_s": top_rate,
        "numa_node": str(row.get("numa_node", "N/A")),
        "handler_numa_nodes": ",".join(str(node) for node in numa["handler_numa_nodes"]),
        "cross_numa_flag": numa["cross_numa_flag"],
        "active_cpu_count": sum(1 for rate in cpu_rates.values() if rate > 0.0),
        "affinity_list": str(row.get("affinity_list", "N/A")),
    }


def irq_balance_score(cpu_rates: Dict[str, float]) -> Dict[str, Union[float, str]]:
    values = [max(0.0, float(v)) for v in cpu_rates.values() if float(v) >= 0.0]
    if not values:
        return {
            "score": 0.0,
            "normalized_entropy": 0.0,
            "coefficient_variation": 0.0,
            "status": "Unavailable",
        }

    total = sum(values)
    if total <= 0.0:
        return {
            "score": 0.0,
            "normalized_entropy": 0.0,
            "coefficient_variation": 0.0,
            "status": "Unavailable",
        }

    n = len(values)
    probs = [v / total for v in values]
    entropy = -sum(p * math.log(p) for p in probs if p > 0.0)
    max_entropy = math.log(max(1, n))
    normalized_entropy = (entropy / max_entropy) if max_entropy > 0.0 else 0.0

    mean = total / n
    variance = sum((v - mean) ** 2 for v in values) / n
    std_dev = math.sqrt(variance)
    cv = (std_dev / mean) if mean > 0.0 else 0.0

    score = max(0.0, min(100.0, normalized_entropy * 100.0))
    if score >= 80.0 and cv < 0.40:
        status = "Balanced"
    elif score >= 60.0 and cv < 0.70:
        status = "Moderately Imbalanced"
    else:
        status = "Highly Imbalanced"

    return {
        "score": round(score, 2),
        "normalized_entropy": round(normalized_entropy, 4),
        "coefficient_variation": round(cv, 4),
        "status": status,
    }


def detect_spikes(values: List[Tuple[float, float]], multiplier: float = 2.0, baseline_points: int = 8) -> List[Dict[str, Union[float, str]]]:
    events: List[Dict[str, Union[float, str]]] = []
    if len(values) < baseline_points + 1:
        return events

    for idx in range(baseline_points, len(values)):
        ts, current = values[idx]
        base_slice = [max(0.0, v) for _, v in values[idx - baseline_points:idx]]
        baseline = sum(base_slice) / len(base_slice) if base_slice else 0.0
        if baseline <= 0:
            continue
        if current >= baseline * multiplier:
            events.append(
                {
                    "timestamp": ts,
                    "current": round(current, 4),
                    "baseline": round(baseline, 4),
                    "ratio": round(current / baseline, 4),
                    "multiplier": multiplier,
                    "type": "spike",
                }
            )
    return events
