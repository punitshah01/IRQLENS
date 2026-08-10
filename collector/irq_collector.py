#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import time
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

_IRQ_PATTERNS = [
    re.compile(r"^(?P<nic>[A-Za-z0-9_.:-]+)[-_](?P<direction>TxRx|txrx|RX|rx|TX|tx)[-_](?P<queue>\d+)$"),
    re.compile(r"^(?P<nic>[A-Za-z0-9_.:-]+)[-_](?P<queue>\d+)[-_](?P<direction>TxRx|txrx|RX|rx|TX|tx)$"),
    re.compile(r"^(?P<nic>[A-Za-z0-9_.:-]+).*?(?P<direction>TxRx|txrx|RX|rx|TX|tx).*?(?P<queue>\d+)$"),
    re.compile(r"^(?P<nic>mlx\d+).*?comp(?P<queue>\d+)$", re.IGNORECASE),
]


def normalize_direction(direction: str) -> str:
    value = (direction or "").lower()
    if value == "rx":
        return "RX"
    if value == "tx":
        return "TX"
    if value == "txrx":
        return "TxRx"
    return "Other"


def classify_irq_name(irq_name: str, nic_hint: str = "") -> Dict[str, str]:
    label = (irq_name or "").strip()
    nic = nic_hint or ""
    queue = ""
    direction = "Other"
    source_class = "other"

    for pattern in _IRQ_PATTERNS:
        match = pattern.match(label)
        if not match:
            continue
        groups = match.groupdict()
        nic = groups.get("nic") or nic
        queue = groups.get("queue") or queue
        direction = normalize_direction(groups.get("direction") or direction)
        source_class = "network"
        break

    lower = label.lower()
    if source_class == "other":
        if nic_hint and nic_hint.lower() in lower:
            nic = nic_hint
            source_class = "network"
        elif any(token in lower for token in ["txrx", "mlx", "eth", "ens", "eno", "enp", "virtio", "bnxt", "ixgbe", "i40e", "ice"]):
            source_class = "network"

    if not queue:
        trailing = re.search(r"(\d+)$", label)
        if trailing and source_class == "network":
            queue = trailing.group(1)

    if direction == "Other":
        if "txrx" in lower or "comp" in lower:
            direction = "TxRx"
        elif re.search(r"(^|[^a-z])rx([^a-z]|$)", lower):
            direction = "RX"
        elif re.search(r"(^|[^a-z])tx([^a-z]|$)", lower):
            direction = "TX"

    return {
        "nic": nic,
        "queue": queue,
        "direction": direction,
        "source_class": source_class,
    }


def parse_interrupts() -> Tuple[List[str], Dict[str, Tuple[str, List[int]]]]:
    lines = Path("/proc/interrupts").read_text(encoding="utf-8", errors="ignore").splitlines()
    header = lines[0].split()
    cpus = [x for x in header if x.startswith("CPU")]

    rows: Dict[str, Tuple[str, List[int]]] = {}
    for ln in lines[1:]:
        if ":" not in ln:
            continue
        left, right = ln.split(":", 1)
        irq = left.strip()
        parts = right.split()
        if len(parts) < len(cpus) + 1:
            continue
        counts = []
        ok = True
        for i in range(len(cpus)):
            try:
                counts.append(int(parts[i]))
            except ValueError:
                ok = False
                break
        if not ok:
            continue
        irq_name = " ".join(parts[len(cpus):]).strip()
        rows[irq] = (irq_name, counts)
    return cpus, rows


def affinity_for_irq(irq: str) -> str:
    p = Path(f"/proc/irq/{irq}/smp_affinity_list")
    if not p.exists():
        return ""
    try:
        return p.read_text(encoding="utf-8", errors="ignore").strip()
    except Exception:
        return ""


def parse_softirqs() -> Dict[str, int]:
    p = Path("/proc/softirqs")
    if not p.exists():
        return {}
    lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
    out: Dict[str, int] = {}
    for ln in lines[1:]:
        if ":" not in ln:
            continue
        name, right = ln.split(":", 1)
        vals = 0
        for tok in right.split():
            try:
                vals += int(tok)
            except ValueError:
                continue
        out[name.strip()] = vals
    return out


def parse_netdev(nic: str = "") -> Dict[str, int]:
    p = Path("/proc/net/dev")
    if not p.exists():
        return {}
    lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()[2:]
    total = {
        "rx_bytes": 0,
        "rx_packets": 0,
        "rx_errs": 0,
        "rx_drop": 0,
        "tx_bytes": 0,
        "tx_packets": 0,
        "tx_errs": 0,
        "tx_drop": 0,
    }
    for ln in lines:
        if ":" not in ln:
            continue
        iface, data = ln.split(":", 1)
        iface = iface.strip()
        if nic and iface != nic:
            continue
        parts = data.split()
        if len(parts) < 16:
            continue
        try:
            total["rx_bytes"] += int(parts[0])
            total["rx_packets"] += int(parts[1])
            total["rx_errs"] += int(parts[2])
            total["rx_drop"] += int(parts[3])
            total["tx_bytes"] += int(parts[8])
            total["tx_packets"] += int(parts[9])
            total["tx_errs"] += int(parts[10])
            total["tx_drop"] += int(parts[11])
        except ValueError:
            continue
    return total


def post_json(url: str, payload: dict, timeout: float = 3.0) -> None:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with _OPENER.open(req, timeout=timeout):
        return


def collect_loop(server: str, sut_ip: str, interval: float, topn: int, nic: str = "") -> None:
    prev: Dict[str, List[int]] = {}
    prev_soft: Dict[str, int] = {}
    prev_net: Dict[str, int] = {}
    while True:
        ts = time.time()
        cpus, rows = parse_interrupts()
        out = []

        deltas = []
        for irq, (name, cur) in rows.items():
            old = prev.get(irq)
            if old is None or len(old) != len(cur):
                continue
            per_cpu = [max(0, cur[i] - old[i]) for i in range(len(cur))]
            total = float(sum(per_cpu)) / interval
            deltas.append((irq, name, per_cpu, total))

        deltas.sort(key=lambda x: x[3], reverse=True)
        for irq, name, per_cpu, total in deltas[:topn]:
            irq_meta = classify_irq_name(name, nic_hint=nic)
            cpu_rates = {str(i): float(per_cpu[i]) / interval for i in range(len(per_cpu)) if per_cpu[i] > 0}
            out.append(
                {
                    "timestamp": ts,
                    "sut_ip": sut_ip,
                    "irq": irq,
                    "irq_name": name,
                    "nic": irq_meta["nic"],
                    "queue": irq_meta["queue"],
                    "direction": irq_meta["direction"],
                    "source_class": irq_meta["source_class"],
                    "total_rate": total,
                    "cpu_rates": cpu_rates,
                    "affinity_list": affinity_for_irq(irq),
                }
            )

        soft = parse_softirqs()
        net = parse_netdev(nic=nic)

        soft_rates: Dict[str, float] = {}
        if prev_soft:
            for k, v in soft.items():
                soft_rates[k] = float(max(0, v - prev_soft.get(k, 0))) / interval

        host_samples = []
        if prev_net:
            rx_bps = float(max(0, net.get("rx_bytes", 0) - prev_net.get("rx_bytes", 0))) / interval
            tx_bps = float(max(0, net.get("tx_bytes", 0) - prev_net.get("tx_bytes", 0))) / interval
            rx_pps = float(max(0, net.get("rx_packets", 0) - prev_net.get("rx_packets", 0))) / interval
            tx_pps = float(max(0, net.get("tx_packets", 0) - prev_net.get("tx_packets", 0))) / interval
            rx_drop_ps = float(max(0, net.get("rx_drop", 0) - prev_net.get("rx_drop", 0))) / interval
            tx_drop_ps = float(max(0, net.get("tx_drop", 0) - prev_net.get("tx_drop", 0))) / interval
            host_samples.append(
                {
                    "timestamp": ts,
                    "sut_ip": sut_ip,
                    "nic": nic,
                    "rx_bps": rx_bps,
                    "tx_bps": tx_bps,
                    "rx_pps": rx_pps,
                    "tx_pps": tx_pps,
                    "rx_drop_ps": rx_drop_ps,
                    "tx_drop_ps": tx_drop_ps,
                    "softirq_rates": soft_rates,
                }
            )

        if out or host_samples:
            post_json(
                f"{server.rstrip('/')}/api/irq/ingest",
                {"samples": out, "host_samples": host_samples},
            )

        prev = {k: v[1] for k, v in rows.items()}
        prev_soft = soft
        prev_net = net
        time.sleep(interval)


def main() -> int:
    ap = argparse.ArgumentParser(description="IRQLENS collector")
    ap.add_argument("--server", required=True, help="Dashboard backend URL, e.g. http://10.0.0.1:8080")
    ap.add_argument("--sut-ip", required=True, help="SUT identifier/IP")
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--topn", type=int, default=64)
    ap.add_argument("--nic", default="", help="Optional NIC name (e.g. ens3np0). Empty means all interfaces.")
    args = ap.parse_args()

    collect_loop(
        server=args.server,
        sut_ip=args.sut_ip,
        interval=args.interval,
        topn=args.topn,
        nic=args.nic,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
