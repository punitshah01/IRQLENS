from __future__ import annotations

import math
from typing import Dict, List, Tuple, Union


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
