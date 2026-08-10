#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import socket
import subprocess
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
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


def parse_cpu_stat() -> Dict[str, List[int]]:
    p = Path("/proc/stat")
    if not p.exists():
        return {}
    out: Dict[str, List[int]] = {}
    for ln in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not ln.startswith("cpu"):
            continue
        parts = ln.split()
        if len(parts) < 8:
            continue
        name = parts[0]
        try:
            vals = [int(x) for x in parts[1:11]]
        except ValueError:
            continue
        out[name] = vals
    return out


def compute_cpu_percentages(prev: Dict[str, List[int]], cur: Dict[str, List[int]]) -> Dict[str, Dict[str, float]]:
    out: Dict[str, Dict[str, float]] = {}
    for name, cur_vals in cur.items():
        old_vals = prev.get(name)
        if not old_vals or len(old_vals) != len(cur_vals):
            continue
        deltas = [max(0, cur_vals[i] - old_vals[i]) for i in range(len(cur_vals))]
        total = float(sum(deltas))
        if total <= 0:
            continue
        idle = float(deltas[3] + (deltas[4] if len(deltas) > 4 else 0))
        irq = float(deltas[6] if len(deltas) > 6 else 0)
        sirq = float(deltas[7] if len(deltas) > 7 else 0)
        out[name] = {
            "cpu_util_pct": max(0.0, min(100.0, (1.0 - (idle / total)) * 100.0)),
            "irq_pct": (irq / total) * 100.0,
            "sirq_pct": (sirq / total) * 100.0,
        }
    return out


def detect_interfaces() -> List[str]:
    p = Path("/proc/net/dev")
    if not p.exists():
        return []
    out = []
    for ln in p.read_text(encoding="utf-8", errors="ignore").splitlines()[2:]:
        if ":" not in ln:
            continue
        iface = ln.split(":", 1)[0].strip()
        if iface and iface != "lo":
            out.append(iface)
    return sorted(set(out))


def parse_netdev_by_interface() -> Dict[str, Dict[str, int]]:
    p = Path("/proc/net/dev")
    out: Dict[str, Dict[str, int]] = {}
    if not p.exists():
        return out
    for ln in p.read_text(encoding="utf-8", errors="ignore").splitlines()[2:]:
        if ":" not in ln:
            continue
        iface, data = ln.split(":", 1)
        iface = iface.strip()
        parts = data.split()
        if len(parts) < 16:
            continue
        try:
            out[iface] = {
                "rx_bytes": int(parts[0]),
                "rx_packets": int(parts[1]),
                "rx_errs": int(parts[2]),
                "rx_drop": int(parts[3]),
                "tx_bytes": int(parts[8]),
                "tx_packets": int(parts[9]),
                "tx_errs": int(parts[10]),
                "tx_drop": int(parts[11]),
            }
        except ValueError:
            continue
    return out


def run_cmd(cmd: List[str]) -> str:
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False)
        return p.stdout or ""
    except Exception as exc:
        return f"ERROR running {' '.join(cmd)}: {exc}\n"


def write_startup_artifacts(outdir: Path, sut_ip: str, interfaces: List[str]) -> Dict[str, object]:
    outdir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    snapshot = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "sut": sut_ip,
        "hostname": socket.gethostname(),
        "interfaces": interfaces,
        "commands": {},
    }
    previews: Dict[str, str] = {}

    commands = {
        "ip_addr": ["ip", "addr"],
        "ip_link": ["ip", "-s", "link"],
        "ip_route": ["ip", "route"],
        "ss_summary": ["ss", "-s"],
        "nstat": ["nstat", "-az"],
        "proc_interrupts": ["cat", "/proc/interrupts"],
        "proc_softirqs": ["cat", "/proc/softirqs"],
    }
    for iface in interfaces:
        commands[f"ethtool_i_{iface}"] = ["ethtool", "-i", iface]

    lines = []
    for name, cmd in commands.items():
        text = run_cmd(cmd)
        snapshot["commands"][name] = text
        previews[name] = "\n".join(text.splitlines()[:24])
        lines.append(f"===== {name} =====\n")
        lines.append(text)
        if not text.endswith("\n"):
            lines.append("\n")

    (outdir / f"network_snapshot_{ts}.txt").write_text("".join(lines), encoding="utf-8")
    (outdir / f"network_snapshot_{ts}.json").write_text(json.dumps(snapshot, indent=2), encoding="utf-8")

    root = ET.Element("irqlens_snapshot")
    ET.SubElement(root, "timestamp_utc").text = snapshot["timestamp_utc"]
    ET.SubElement(root, "sut").text = snapshot["sut"]
    ET.SubElement(root, "hostname").text = snapshot["hostname"]
    ifs = ET.SubElement(root, "interfaces")
    for iface in interfaces:
        ET.SubElement(ifs, "interface").text = iface
    cmds = ET.SubElement(root, "commands")
    for name, text in snapshot["commands"].items():
        node = ET.SubElement(cmds, "command", name=name)
        node.text = text
    ET.ElementTree(root).write(outdir / f"network_snapshot_{ts}.xml", encoding="utf-8", xml_declaration=True)
    return {
        "snapshot_time": snapshot["timestamp_utc"],
        "interfaces": interfaces,
        "command_preview": previews,
        "files": {
            "txt": str((outdir / f"network_snapshot_{ts}.txt").resolve()),
            "json": str((outdir / f"network_snapshot_{ts}.json").resolve()),
            "xml": str((outdir / f"network_snapshot_{ts}.xml").resolve()),
            "csv": str((outdir / "irqlens_samples.csv").resolve()),
            "jsonl": str((outdir / "irqlens_samples.jsonl").resolve()),
            "latest_xml": str((outdir / "irqlens_latest.xml").resolve()),
            "txt_stream": str((outdir / "irqlens_samples.txt").resolve()),
        },
    }


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


def parse_softirqs_per_cpu() -> Dict[str, int]:
    p = Path("/proc/softirqs")
    if not p.exists():
        return {}
    lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
    if not lines:
        return {}
    header = lines[0].split()
    cpus = [x for x in header if x.startswith("CPU")]
    totals = [0 for _ in cpus]
    for ln in lines[1:]:
        if ":" not in ln:
            continue
        _, right = ln.split(":", 1)
        parts = right.split()
        for i in range(min(len(cpus), len(parts))):
            try:
                totals[i] += int(parts[i])
            except ValueError:
                continue
    return {str(i): totals[i] for i in range(len(cpus))}


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
    prev_soft_cpu: Dict[str, int] = {}
    prev_cpu_stat: Dict[str, List[int]] = {}
    prev_iface = parse_netdev_by_interface()
    interfaces = detect_interfaces()

    outdir = Path(os.getenv("IRQLENS_ARTIFACT_DIR", "./artifacts"))
    startup_meta = write_startup_artifacts(outdir, sut_ip=sut_ip, interfaces=interfaces)

    csv_path = outdir / "irqlens_samples.csv"
    jsonl_path = outdir / "irqlens_samples.jsonl"
    txt_path = outdir / "irqlens_samples.txt"
    csv_exists = csv_path.exists()
    outdir.mkdir(parents=True, exist_ok=True)

    if not csv_exists:
        with csv_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow([
                "timestamp_utc",
                "sut",
                "nic",
                "rx_bps",
                "tx_bps",
                "rx_pps",
                "tx_pps",
                "softirq_total",
                "top_irq",
                "top_irq_rate",
            ])

    while True:
        ts = time.time()
        ts_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S %z")
        cpus, rows = parse_interrupts()
        cpu_stat = parse_cpu_stat()
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
        soft_cpu = parse_softirqs_per_cpu()
        net = parse_netdev(nic=nic)
        iface_now = parse_netdev_by_interface()

        soft_rates: Dict[str, float] = {}
        if prev_soft:
            for k, v in soft.items():
                soft_rates[k] = float(max(0, v - prev_soft.get(k, 0))) / interval

        soft_cpu_rates: Dict[str, float] = {}
        if prev_soft_cpu:
            for k, v in soft_cpu.items():
                soft_cpu_rates[k] = float(max(0, v - prev_soft_cpu.get(k, 0))) / interval

        irq_total_cpu_rates: Dict[str, float] = {str(i): 0.0 for i in range(len(cpus))}
        for _, _, per_cpu, _ in deltas:
            for i, val in enumerate(per_cpu):
                irq_total_cpu_rates[str(i)] += float(val) / interval

        cpu_pct = compute_cpu_percentages(prev_cpu_stat, cpu_stat)
        cpu_util_pct = {}
        irq_pct = {}
        sirq_pct = {}
        for i in range(len(cpus)):
            key = f"cpu{i}"
            metrics = cpu_pct.get(key, {})
            cpu_util_pct[str(i)] = float(metrics.get("cpu_util_pct", 0.0))
            irq_pct[str(i)] = float(metrics.get("irq_pct", 0.0))
            sirq_pct[str(i)] = float(metrics.get("sirq_pct", 0.0))

        per_interface_rates: Dict[str, Dict[str, float]] = {}
        for iface, curvals in iface_now.items():
            old = prev_iface.get(iface)
            if not old:
                continue
            per_interface_rates[iface] = {
                "rx_bps": float(max(0, curvals["rx_bytes"] - old.get("rx_bytes", 0))) / interval,
                "tx_bps": float(max(0, curvals["tx_bytes"] - old.get("tx_bytes", 0))) / interval,
                "rx_pps": float(max(0, curvals["rx_packets"] - old.get("rx_packets", 0))) / interval,
                "tx_pps": float(max(0, curvals["tx_packets"] - old.get("tx_packets", 0))) / interval,
                "rx_drop_ps": float(max(0, curvals["rx_drop"] - old.get("rx_drop", 0))) / interval,
                "tx_drop_ps": float(max(0, curvals["tx_drop"] - old.get("tx_drop", 0))) / interval,
            }

        host_samples = []
        if prev_net:
            rx_bps = float(max(0, net.get("rx_bytes", 0) - prev_net.get("rx_bytes", 0))) / interval
            tx_bps = float(max(0, net.get("tx_bytes", 0) - prev_net.get("tx_bytes", 0))) / interval
            rx_pps = float(max(0, net.get("rx_packets", 0) - prev_net.get("rx_packets", 0))) / interval
            tx_pps = float(max(0, net.get("tx_packets", 0) - prev_net.get("tx_packets", 0))) / interval
            rx_drop_ps = float(max(0, net.get("rx_drop", 0) - prev_net.get("rx_drop", 0))) / interval
            tx_drop_ps = float(max(0, net.get("tx_drop", 0) - prev_net.get("tx_drop", 0))) / interval
            top_irq_name = ""
            top_irq_rate = 0.0
            if deltas:
                top_irq_name = deltas[0][1]
                top_irq_rate = deltas[0][3]

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
                    "details": {
                        "timestamp": ts_iso,
                        "interfaces": interfaces,
                        "startup": startup_meta,
                        "per_interface_rates": per_interface_rates,
                        "cpu_util_pct": cpu_util_pct,
                        "irq_pct": irq_pct,
                        "sirq_pct": sirq_pct,
                        "irq_total_cpu": irq_total_cpu_rates,
                        "softirq_total_cpu": soft_cpu_rates,
                    },
                }
            )

            softirq_total = sum(soft_rates.values()) if soft_rates else 0.0
            with csv_path.open("a", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                writer.writerow([
                    ts_iso,
                    sut_ip,
                    nic,
                    f"{rx_bps:.3f}",
                    f"{tx_bps:.3f}",
                    f"{rx_pps:.3f}",
                    f"{tx_pps:.3f}",
                    f"{softirq_total:.3f}",
                    top_irq_name,
                    f"{top_irq_rate:.3f}",
                ])

            snapshot = {
                "timestamp": ts_iso,
                "sut": sut_ip,
                "nic": nic,
                "host": host_samples[0],
                "top_irqs": out[: min(20, len(out))],
            }
            with jsonl_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(snapshot, separators=(",", ":")) + "\n")

            with txt_path.open("a", encoding="utf-8") as fh:
                fh.write(f"[{ts_iso}] {sut_ip} nic={nic or 'auto'} RX={rx_pps:.0f}pps TX={tx_pps:.0f}pps softirq={softirq_total:.0f}/s top_irq={top_irq_name} {top_irq_rate:.1f}/s\n")

            xml_root = ET.Element("irqlens_sample")
            ET.SubElement(xml_root, "timestamp").text = ts_iso
            ET.SubElement(xml_root, "sut").text = sut_ip
            ET.SubElement(xml_root, "nic").text = nic
            host_node = ET.SubElement(xml_root, "host")
            for k, v in host_samples[0].items():
                if isinstance(v, (int, float, str)):
                    ET.SubElement(host_node, k).text = str(v)
            top_node = ET.SubElement(xml_root, "top_irqs")
            for item in out[: min(20, len(out))]:
                row = ET.SubElement(top_node, "irq")
                ET.SubElement(row, "id").text = str(item.get("irq", ""))
                ET.SubElement(row, "name").text = str(item.get("irq_name", ""))
                ET.SubElement(row, "rate").text = str(item.get("total_rate", 0.0))
                ET.SubElement(row, "direction").text = str(item.get("direction", "Other"))
                ET.SubElement(row, "queue").text = str(item.get("queue", ""))
            ET.ElementTree(xml_root).write(outdir / "irqlens_latest.xml", encoding="utf-8", xml_declaration=True)

        if out or host_samples:
            post_json(
                f"{server.rstrip('/')}/api/irq/ingest",
                {"samples": out, "host_samples": host_samples},
            )

        prev = {k: v[1] for k, v in rows.items()}
        prev_soft = soft
        prev_soft_cpu = soft_cpu
        prev_net = net
        prev_cpu_stat = cpu_stat
        prev_iface = iface_now
        interfaces = detect_interfaces()
        time.sleep(interval)


def main() -> int:
    ap = argparse.ArgumentParser(description="IRQLENS collector")
    ap.add_argument("--server", required=True, help="Dashboard backend URL, e.g. http://10.0.0.1:8080")
    ap.add_argument("--sut-ip", required=True, help="SUT identifier/IP")
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--topn", type=int, default=64)
    ap.add_argument("--nic", default="", help="Optional NIC name (e.g. ens3np0). Empty means auto/all interfaces.")
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
