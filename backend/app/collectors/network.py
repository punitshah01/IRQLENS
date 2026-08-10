from __future__ import annotations

import os
import socket
from pathlib import Path
from typing import Dict, List, Tuple

from .base import read_first_line, read_text_safe


class NetworkCollector:
    def __init__(self) -> None:
        self._prev: Dict[str, Dict[str, int]] = {}

    def discover_interfaces(self) -> List[str]:
        interfaces = set()
        sysfs = Path("/sys/class/net")
        if sysfs.exists():
            for child in sysfs.iterdir():
                interfaces.add(child.name)
        text = read_text_safe("/proc/net/dev")
        for line in text.splitlines()[2:]:
            if ":" not in line:
                continue
            iface = line.split(":", 1)[0].strip()
            if iface:
                interfaces.add(iface)
        return sorted(interfaces)

    def _netdev(self) -> Dict[str, Dict[str, int]]:
        out: Dict[str, Dict[str, int]] = {}
        text = read_text_safe("/proc/net/dev")
        for line in text.splitlines()[2:]:
            if ":" not in line:
                continue
            iface, values = line.split(":", 1)
            iface = iface.strip()
            parts = values.split()
            if len(parts) < 16:
                continue
            try:
                out[iface] = {
                    "rx_bytes": int(parts[0]),
                    "rx_packets": int(parts[1]),
                    "rx_errors": int(parts[2]),
                    "rx_drops": int(parts[3]),
                    "tx_bytes": int(parts[8]),
                    "tx_packets": int(parts[9]),
                    "tx_errors": int(parts[10]),
                    "tx_drops": int(parts[11]),
                }
            except ValueError:
                continue
        return out

    def _iface_info(self, interface: str, ts: float) -> Dict[str, object]:
        base = Path("/sys/class/net") / interface
        operstate = read_first_line(str(base / "operstate"), "N/A")
        mtu = read_first_line(str(base / "mtu"), "N/A")
        address = read_first_line(str(base / "address"), "N/A")
        speed = read_first_line(str(base / "speed"), "N/A")
        duplex = read_first_line(str(base / "duplex"), "N/A")
        driver = "N/A"
        drv_link = base / "device" / "driver"
        if drv_link.exists():
            try:
                driver = os.path.basename(os.readlink(str(drv_link)))
            except OSError:
                driver = "N/A"

        ipv4: List[str] = []
        ipv6: List[str] = []
        try:
            for item in socket.getaddrinfo(None, 0):
                _ = item
        except Exception:
            pass

        # IP addresses are populated by command collector using `ip -br addr` when available.
        return {
            "timestamp": ts,
            "name": interface,
            "state": operstate,
            "mtu": int(mtu) if mtu.isdigit() else None,
            "mac": address,
            "speed_mbps": int(speed) if speed.lstrip("-").isdigit() and int(speed) >= 0 else None,
            "duplex": duplex,
            "driver": driver,
            "firmware": "N/A",
            "ipv4": ipv4,
            "ipv6": ipv6,
        }

    def collect(self, elapsed: float, ts: float) -> Tuple[List[Dict[str, object]], Dict[str, float], List[Dict[str, object]]]:
        cur = self._netdev()
        interfaces = sorted(cur.keys())
        samples: List[Dict[str, object]] = []
        global_totals = {
            "interfaces": float(len(interfaces)),
            "interfaces_up": 0.0,
            "interfaces_down": 0.0,
            "rx_bps": 0.0,
            "tx_bps": 0.0,
            "rx_pps": 0.0,
            "tx_pps": 0.0,
            "rx_err_ps": 0.0,
            "tx_err_ps": 0.0,
            "rx_drop_ps": 0.0,
            "tx_drop_ps": 0.0,
        }
        iface_infos: List[Dict[str, object]] = []

        for iface in interfaces:
            info = self._iface_info(iface, ts)
            iface_infos.append(info)
            if info.get("state") == "up":
                global_totals["interfaces_up"] += 1
            else:
                global_totals["interfaces_down"] += 1

            now = cur.get(iface, {})
            old = self._prev.get(iface)

            if not old:
                rx_bps = 0.0
                tx_bps = 0.0
                rx_pps = 0.0
                tx_pps = 0.0
                rx_err_ps = 0.0
                tx_err_ps = 0.0
                rx_drop_ps = 0.0
                tx_drop_ps = 0.0
            else:
                def delta(key: str) -> int:
                    val = int(now.get(key, 0)) - int(old.get(key, 0))
                    return val if val >= 0 else int(now.get(key, 0))

                rx_bps = float(delta("rx_bytes")) / elapsed
                tx_bps = float(delta("tx_bytes")) / elapsed
                rx_pps = float(delta("rx_packets")) / elapsed
                tx_pps = float(delta("tx_packets")) / elapsed
                rx_err_ps = float(delta("rx_errors")) / elapsed
                tx_err_ps = float(delta("tx_errors")) / elapsed
                rx_drop_ps = float(delta("rx_drops")) / elapsed
                tx_drop_ps = float(delta("tx_drops")) / elapsed

            samples.append(
                {
                    "timestamp": ts,
                    "interface": iface,
                    **now,
                    "rx_bps": rx_bps,
                    "tx_bps": tx_bps,
                    "rx_pps": rx_pps,
                    "tx_pps": tx_pps,
                    "rx_err_ps": rx_err_ps,
                    "tx_err_ps": tx_err_ps,
                    "rx_drop_ps": rx_drop_ps,
                    "tx_drop_ps": tx_drop_ps,
                }
            )

            global_totals["rx_bps"] += rx_bps
            global_totals["tx_bps"] += tx_bps
            global_totals["rx_pps"] += rx_pps
            global_totals["tx_pps"] += tx_pps
            global_totals["rx_err_ps"] += rx_err_ps
            global_totals["tx_err_ps"] += tx_err_ps
            global_totals["rx_drop_ps"] += rx_drop_ps
            global_totals["tx_drop_ps"] += tx_drop_ps

        self._prev = cur
        return samples, global_totals, iface_infos
