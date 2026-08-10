from __future__ import annotations

from backend.app.services.visualization import detect_spikes, irq_balance_score


def test_irq_balance_score_balanced_vs_imbalanced():
    balanced = irq_balance_score({"0": 10.0, "1": 10.0, "2": 10.0, "3": 10.0})
    imbalanced = irq_balance_score({"0": 40.0, "1": 0.0, "2": 0.0, "3": 0.0})

    assert float(balanced["score"]) > float(imbalanced["score"])
    assert balanced["status"] in {"Balanced", "Moderately Imbalanced", "Highly Imbalanced"}


def test_detect_spikes_simple_threshold():
    values = [
        (1.0, 10.0),
        (2.0, 11.0),
        (3.0, 10.5),
        (4.0, 9.8),
        (5.0, 10.2),
        (6.0, 10.1),
        (7.0, 10.3),
        (8.0, 10.0),
        (9.0, 30.0),
    ]
    events = detect_spikes(values, multiplier=2.0, baseline_points=8)
    assert len(events) == 1
    assert events[0]["timestamp"] == 9.0
