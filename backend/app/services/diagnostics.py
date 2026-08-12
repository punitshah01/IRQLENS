from __future__ import annotations

import os
import re
import shutil
import time
from html import escape
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from ..collectors import DiagnosticCommandCollector, NetworkCollector, SystemCollector
from ..config import Settings
from ..models import CollectionSession, ExportFile
from ..store import SqliteStore
from .exporter import ExportEngine


class DiagnosticSessionService:
    def __init__(self, settings: Settings, store: SqliteStore, collector_version: str = "0.2.0") -> None:
        self.settings = settings
        self.store = store
        self.collector_version = collector_version
        self.network_collector = NetworkCollector()
        self.system_collector = SystemCollector()
        self.command_collector = DiagnosticCommandCollector(settings.command_timeout_seconds)
        self.exporter = ExportEngine()
        self._active_session_id: str = ""

    @property
    def active_session_id(self) -> str:
        return self._active_session_id

    def _safe_session_id(self, session_id: str) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)

    def validate_session_name(self, session_name: str) -> Tuple[bool, str]:
        raw = (session_name or "").strip()
        if not raw:
            return False, "session_name is required"
        if "/" in raw or "\\" in raw:
            return False, "session_name must not contain path separators"
        if ".." in raw:
            return False, "session_name must not contain traversal sequences"
        if raw.startswith("."):
            return False, "session_name must start with an alphanumeric character"
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", raw):
            return False, "session_name contains unsupported characters"
        return True, raw

    def _session_dir(self, session_id: str) -> Path:
        return self.settings.output_dir / self._safe_session_id(session_id)

    def _ensure_within_output(self, target: Path) -> Path:
        resolved = target.resolve()
        base = self.settings.output_dir.resolve()
        if resolved != base and base not in resolved.parents:
            raise ValueError(f"path escapes output_dir: {resolved}")
        return resolved

    def start(self, session_name: str, categories: List[str], system_hostname: str, os_distribution: str, kernel: str, sut_id: str = "") -> CollectionSession:
        ok, safe_name = self.validate_session_name(session_name)
        if not ok:
            raise ValueError(safe_name)
        ts = time.time()
        session_id = safe_name
        outdir = self._session_dir(session_id)
        if outdir.exists():
            raise FileExistsError(f"session directory already exists: {outdir}")
        outdir.mkdir(parents=True, exist_ok=True)

        session = CollectionSession(
            session_id=session_id,
            sut_id=sut_id,
            status="running",
            start_time=ts,
            end_time=None,
            hostname=system_hostname,
            os_distribution=os_distribution,
            kernel=kernel,
            collector_version=self.collector_version,
            output_dir=str(outdir),
            categories=categories,
            error="",
        )
        self.store.create_session(session)
        self._active_session_id = session_id

        metadata_dir = outdir / "metadata"
        metadata_dir.mkdir(parents=True, exist_ok=True)
        self.exporter.write_json(metadata_dir / "metadata.json", session.model_dump())
        self.exporter.write_json(outdir / "summary.json", {
            "session_name": session_id,
            "sut_id": sut_id,
            "hostname": system_hostname,
            "status": "running",
            "start_time": ts,
            "end_time": None,
            "duration_seconds": 0,
            "selected_capture_categories": categories,
            "output_dir": str(outdir),
        })
        return session

    def collect_snapshot(self, session_id: str, categories: List[str], sut_id: str = "local") -> List[ExportFile]:
        outdir = self._session_dir(session_id)
        outdir.mkdir(parents=True, exist_ok=True)

        interfaces = self.network_collector.discover_interfaces() if sut_id == "local" else [i.name for i in self.store.latest_interfaces(sut_id)]
        system_row = self.store.latest_system(sut_id)
        system = system_row.model_dump() if system_row else self.system_collector.collect(time.time())
        ts = time.time()

        if sut_id == "local":
            net_rows_raw, net_global, iface_infos = self.network_collector.collect(1.0, ts)
        else:
            net_rows = [r.model_dump() for r in self.store.latest_network(sut_id, limit=512)]
            iface_infos = [i.model_dump() for i in self.store.latest_interfaces(sut_id)]
            net_rows_raw = net_rows
            net_global = {
                "interfaces": float(len(iface_infos)),
                "interfaces_up": float(len([i for i in iface_infos if i.get("state") == "up"])),
                "interfaces_down": float(len([i for i in iface_infos if i.get("state") != "up"])),
                "rx_bps": float(sum(float(x.get("rx_bps", 0.0)) for x in net_rows)),
                "tx_bps": float(sum(float(x.get("tx_bps", 0.0)) for x in net_rows)),
                "rx_pps": float(sum(float(x.get("rx_pps", 0.0)) for x in net_rows)),
                "tx_pps": float(sum(float(x.get("tx_pps", 0.0)) for x in net_rows)),
                "rx_err_ps": float(sum(float(x.get("rx_err_ps", 0.0)) for x in net_rows)),
                "tx_err_ps": float(sum(float(x.get("tx_err_ps", 0.0)) for x in net_rows)),
                "rx_drop_ps": float(sum(float(x.get("rx_drop_ps", 0.0)) for x in net_rows)),
                "tx_drop_ps": float(sum(float(x.get("tx_drop_ps", 0.0)) for x in net_rows)),
            }
        files: List[ExportFile] = []
        selected_host = sut_id

        if "irq" in categories:
            irq_rows = [row.model_dump() for row in self.store.latest_irq(selected_host, limit=512)]
            irq_dir = outdir / "irq"
            files.extend(self._emit_category_files(irq_dir, "irqtop", {"host": selected_host, "rows": irq_rows}, irq_rows))

        if "softirq" in categories:
            soft = self.store.latest_softirq(selected_host)
            soft_payload = soft.model_dump() if soft else {"host": selected_host, "sample": None}
            soft_rows = []
            if soft:
                soft_rows = [{"class": k, "rate": v} for k, v in soft.rates.items()]
            soft_dir = outdir / "softirq"
            files.extend(self._emit_category_files(soft_dir, "softirq", soft_payload, soft_rows))

        if "network" in categories:
            category_dir = outdir / "network"
            network_json = {
                "timestamp": ts,
                "global": net_global,
                "interfaces": iface_infos,
                "samples": net_rows_raw,
            }
            files.extend(self._emit_category_files(category_dir, "network", network_json, net_rows_raw))

        if "interfaces" in categories:
            interfaces_dir = outdir / "interfaces"
            files.extend(self._emit_category_files(interfaces_dir, "interfaces", {"interfaces": iface_infos}, iface_infos))

        if "system" in categories:
            category_dir = outdir / "system"
            files.extend(self._emit_category_files(category_dir, "system", system, [system]))

        command_categories = [c for c in categories if c in {"network", "interfaces", "routes", "sockets", "ethtool"}]
        if command_categories and sut_id == "local":
            command_dir = outdir / "commands"
            commands = self.command_collector.available_commands(interfaces=interfaces, categories=command_categories)
            command_rows = []
            for category, command_list in commands.items():
                for cmd in command_list:
                    iface = ""
                    if cmd and cmd[0] == "ethtool" and len(cmd) >= 2 and not cmd[1].startswith("-"):
                        iface = cmd[1]
                    result = self.command_collector.run(category=category, command=cmd, interface=iface)
                    command_rows.append(result.model_dump())
            files.extend(self._emit_category_files(command_dir, "commands", {"commands": command_rows}, command_rows))
        elif command_categories:
            command_dir = outdir / "commands"
            note = {
                "sut_id": sut_id,
                "status": "remote-agent-required",
                "message": "Remote command diagnostics must be executed by IRQLENS SUT Agent on target host.",
                "timestamp": ts,
            }
            files.extend(self._emit_category_files(command_dir, "commands", note, [note]))

        latest_dir = self.settings.output_dir / "latest"
        latest_dir.mkdir(parents=True, exist_ok=True)
        latest_link = latest_dir / "session.txt"
        latest_link.write_text(session_id, encoding="utf-8")

        self.store.add_session_files(session_id, files)
        return files

    def _emit_category_files(self, category_dir: Path, category: str, payload: object, rows: List[dict]) -> List[ExportFile]:
        category_dir.mkdir(parents=True, exist_ok=True)
        out: List[ExportFile] = []

        json_path = category_dir / f"{category}.json"
        self.exporter.write_json(json_path, payload)
        out.append(self._to_export_file(json_path, category, "json"))

        csv_path = category_dir / f"{category}.csv"
        self.exporter.write_csv(csv_path, rows)
        out.append(self._to_export_file(csv_path, category, "csv"))

        return out

    def _to_export_file(self, path: Path, category: str, fmt: str) -> ExportFile:
        return ExportFile(
            name=path.name,
            category=category,
            format=fmt,
            path=str(path),
            size_bytes=path.stat().st_size if path.exists() else 0,
        )

    def stop(self, session_id: str, reason: str = "manual") -> Optional[CollectionSession]:
        session = self.store.get_session(session_id)
        if not session:
            return None
        final_ts = time.time()
        final_status = "stopped" if reason == "manual" else "completed"
        self.store.update_session_status(session_id, status=final_status, end_time=final_ts, error="" if reason in {"manual", "duration-complete"} else reason)
        if self._active_session_id == session_id:
            self._active_session_id = ""
        updated = self.store.get_session(session_id)
        if updated:
            summary_path = Path(updated.output_dir) / "summary.json"
            self.exporter.write_json(summary_path, {
                "session_name": updated.session_id,
                "sut_id": updated.sut_id,
                "hostname": updated.hostname,
                "status": updated.status,
                "start_time": updated.start_time,
                "end_time": updated.end_time,
                "duration_seconds": max(0, int((updated.end_time or final_ts) - updated.start_time)),
                "selected_capture_categories": updated.categories,
                "output_dir": updated.output_dir,
                "error": updated.error,
            })
            metadata_dir = Path(updated.output_dir) / "metadata"
            metadata_dir.mkdir(parents=True, exist_ok=True)
            self.exporter.write_json(metadata_dir / "metadata.json", updated.model_dump())
            self._write_timeseries_capture(updated)
        return updated

    def list_files(self, session_id: str) -> List[ExportFile]:
        return self.store.session_files(session_id)

    def archive_session(self, session_id: str) -> Optional[Path]:
        session = self.store.get_session(session_id)
        if not session:
            return None
        session_dir = Path(session.output_dir)
        if not session_dir.exists():
            return None
        archive_base = self.settings.output_dir / f"{session_id}"
        archive_file = shutil.make_archive(str(archive_base), "zip", root_dir=str(session_dir))
        archive_path = Path(archive_file)
        self.store.add_session_files(
            session_id,
            [
                ExportFile(
                    name=archive_path.name,
                    category="session",
                    format="zip",
                    path=str(archive_path),
                    size_bytes=archive_path.stat().st_size,
                )
            ],
        )
        return archive_path

    def delete_session(self, session_id: str) -> bool:
        session = self.store.get_session(session_id)
        if not session:
            return False

        session_dir = self._ensure_within_output(Path(session.output_dir))
        if session_dir.exists() and session_dir.is_dir():
            shutil.rmtree(session_dir, ignore_errors=True)

        # Remove legacy artifacts that may exist at output root.
        for candidate in [self.settings.output_dir / f"{session_id}.zip", self.settings.output_dir / f"{session_id}.html"]:
            try:
                resolved = self._ensure_within_output(candidate)
                if resolved.exists() and resolved.is_file():
                    resolved.unlink()
            except Exception:
                continue

        return self.store.delete_session(session_id)

    def _read_json(self, path: Path, default: Any) -> Any:
        if not path.exists() or not path.is_file():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default

    def _render_no_data(self, title: str) -> str:
        return f"<section class=\"card\"><h3>{escape(title)}</h3><div class=\"empty\">No data captured for this category.</div></section>"

    def _safe_float(self, value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except Exception:
            return default

    def _series_stats(self, values: List[Any]) -> Dict[str, Optional[float]]:
        parsed: List[float] = []
        for value in values:
            try:
                parsed.append(float(value))
            except Exception:
                continue
        if not parsed:
            return {"min": None, "max": None, "avg": None, "latest": None}
        return {
            "min": min(parsed),
            "max": max(parsed),
            "avg": sum(parsed) / len(parsed),
            "latest": parsed[-1],
        }

    def _integrate_series(self, points: List[Dict[str, Any]], key: str) -> float:
        if len(points) < 2:
            return 0.0
        total = 0.0
        prev = points[0]
        prev_ts = self._safe_float(prev.get("timestamp"))
        prev_val = self._safe_float(prev.get(key))
        for point in points[1:]:
            ts = self._safe_float(point.get("timestamp"))
            dt = max(0.0, ts - prev_ts)
            total += prev_val * dt
            prev_ts = ts
            prev_val = self._safe_float(point.get(key))
        return total

    def _format_rate(self, bps: Optional[float]) -> str:
        value = self._safe_float(bps)
        units = ["B/s", "KiB/s", "MiB/s", "GiB/s", "TiB/s"]
        idx = 0
        while value >= 1024.0 and idx < len(units) - 1:
            value /= 1024.0
            idx += 1
        return f"{value:.2f} {units[idx]}"

    def _format_bytes(self, bytes_value: Optional[float]) -> str:
        value = self._safe_float(bytes_value)
        units = ["B", "KiB", "MiB", "GiB", "TiB"]
        idx = 0
        while value >= 1024.0 and idx < len(units) - 1:
            value /= 1024.0
            idx += 1
        return f"{value:.2f} {units[idx]}"

    def _svg_line_chart(self, points: List[Dict[str, Any]], y_key: str, color: str = "#1557c0") -> str:
        if len(points) < 2:
            return "<div class=\"empty\">Not enough data points to render chart.</div>"

        chart_data: List[Tuple[float, float]] = []
        for point in points:
            ts = point.get("timestamp")
            y = point.get(y_key)
            try:
                ts_f = float(ts)
                y_f = float(y)
            except Exception:
                continue
            chart_data.append((ts_f, y_f))

        if len(chart_data) < 2:
            return "<div class=\"empty\">Not enough valid data to render chart.</div>"

        width = 880
        height = 220
        left = 42
        right = 10
        top = 10
        bottom = 26
        plot_w = width - left - right
        plot_h = height - top - bottom

        x_min = min(pt[0] for pt in chart_data)
        x_max = max(pt[0] for pt in chart_data)
        y_min = min(pt[1] for pt in chart_data)
        y_max = max(pt[1] for pt in chart_data)
        if x_max <= x_min:
            x_max = x_min + 1.0
        if y_max <= y_min:
            y_max = y_min + 1.0

        poly = []
        for x_ts, y_val in chart_data:
            x = left + ((x_ts - x_min) / (x_max - x_min)) * plot_w
            y = top + (1.0 - ((y_val - y_min) / (y_max - y_min))) * plot_h
            poly.append(f"{x:.1f},{y:.1f}")

        grid_lines = []
        for i in range(5):
            frac = i / 4.0
            y = top + frac * plot_h
            val = y_max - frac * (y_max - y_min)
            grid_lines.append(f"<line x1=\"{left}\" y1=\"{y:.1f}\" x2=\"{left + plot_w}\" y2=\"{y:.1f}\" stroke=\"#ecf1f8\" stroke-width=\"1\" />")
            grid_lines.append(f"<text x=\"6\" y=\"{y + 4:.1f}\" font-size=\"10\" fill=\"#6b778c\">{val:.2f}</text>")

        for i in range(5):
            frac = i / 4.0
            x = left + frac * plot_w
            tick_ts = x_min + frac * (x_max - x_min)
            tick_label = time.strftime("%H:%M:%S", time.localtime(tick_ts))
            grid_lines.append(f"<line x1=\"{x:.1f}\" y1=\"{top}\" x2=\"{x:.1f}\" y2=\"{top + plot_h}\" stroke=\"#f4f7fb\" stroke-width=\"1\" />")
            grid_lines.append(f"<text x=\"{x - 22:.1f}\" y=\"{height - 8}\" font-size=\"10\" fill=\"#6b778c\">{tick_label}</text>")

        return (
            f"<svg class=\"chart-svg\" viewBox=\"0 0 {width} {height}\" role=\"img\" aria-label=\"time series\">"
            f"{''.join(grid_lines)}"
            f"<polyline fill=\"none\" stroke=\"{escape(color)}\" stroke-width=\"2.2\" points=\"{' '.join(poly)}\" />"
            "</svg>"
        )

    def _write_timeseries_capture(self, session: CollectionSession) -> None:
        session_dir = Path(session.output_dir)
        session_dir.mkdir(parents=True, exist_ok=True)
        ts_dir = session_dir / "timeseries"
        ts_dir.mkdir(parents=True, exist_ok=True)

        start_ts = float(session.start_time)
        end_ts = float(session.end_time if session.end_time is not None else time.time())
        sut_id = session.sut_id or "local"

        cpu_util_samples = self.store.cpu_utilization_series(sut_id, start_ts, end_ts)
        cpu_util_payload = {
            "sut_id": sut_id,
            "start_time": start_ts,
            "end_time": end_ts,
            "samples": [
                {
                    "timestamp": self._safe_float(item.get("timestamp")),
                    "avg_util_percent": self._safe_float(item.get("avg")),
                    "max_util_percent": self._safe_float(item.get("max")),
                    "cpus": item.get("cpus", {}),
                }
                for item in cpu_util_samples
            ],
        }

        system_series = self.store.system_series(sut_id, start_ts, end_ts)
        cpu_freq_payload = {
            "sut_id": sut_id,
            "start_time": start_ts,
            "end_time": end_ts,
            "samples": [
                {
                    "timestamp": self._safe_float(item.get("timestamp")),
                    "cpu_mhz": self._safe_float(item.get("cpu_mhz")),
                }
                for item in system_series
                if item.get("cpu_mhz") is not None
            ],
        }

        network_points = self.store.network_rate_series(sut_id, start_ts, end_ts)
        network_payload = {
            "sut_id": sut_id,
            "start_time": start_ts,
            "end_time": end_ts,
            "samples": network_points,
        }

        iface_payload = {
            "sut_id": sut_id,
            "start_time": start_ts,
            "end_time": end_ts,
            "interfaces": self.store.network_interface_series(sut_id, start_ts, end_ts),
        }

        irq_payload = {
            "sut_id": sut_id,
            "start_time": start_ts,
            "end_time": end_ts,
            "samples": self.store.irq_rate_series(sut_id, start_ts, end_ts),
        }
        irq_top_payload = self.store.irq_source_series(sut_id, start_ts, end_ts)

        soft_series = self.store.softirq_series(sut_id, start_ts, end_ts)
        soft_payload = {
            "sut_id": sut_id,
            "start_time": start_ts,
            "end_time": end_ts,
            "samples": [
                {
                    "timestamp": self._safe_float(item.get("timestamp")),
                    "rates": item.get("rates", {}),
                    "total_rate": sum(self._safe_float(v) for v in (item.get("rates", {}) or {}).values()),
                }
                for item in soft_series
            ],
        }

        file_specs = [
            (ts_dir / "cpu" / "cpu_utilization_timeseries.json", cpu_util_payload),
            (ts_dir / "cpu" / "cpu_frequency_timeseries.json", cpu_freq_payload),
            (ts_dir / "network" / "network_timeseries.json", network_payload),
            (ts_dir / "network" / "network_interfaces_timeseries.json", iface_payload),
            (ts_dir / "irq" / "irq_timeseries.json", irq_payload),
            (ts_dir / "irq" / "irq_top_sources.json", irq_top_payload),
            (ts_dir / "softirq" / "softirq_timeseries.json", soft_payload),
        ]

        out_files: List[ExportFile] = []
        for path, payload in file_specs:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.exporter.write_json(path, payload)
            out_files.append(
                ExportFile(
                    name=path.name,
                    category="timeseries",
                    format="json",
                    path=str(path),
                    size_bytes=path.stat().st_size if path.exists() else 0,
                )
            )
        if out_files:
            self.store.add_session_files(session.session_id, out_files)

    def generate_html_report(self, session_id: str) -> Optional[Path]:
        session = self.store.get_session(session_id)
        if not session:
            return None

        session_dir = Path(session.output_dir)
        if not session_dir.exists() or not session_dir.is_dir():
            return None

        files = self.store.session_files(session_id)
        grouped: Dict[str, List[ExportFile]] = {}
        for item in files:
            grouped.setdefault(item.category or "other", []).append(item)

        duration = 0
        if session.end_time:
            duration = max(0, int(session.end_time - session.start_time))

        system_payload = self._read_json(session_dir / "system" / "system.json", {})

        cpu_util_payload = self._read_json(session_dir / "timeseries" / "cpu" / "cpu_utilization_timeseries.json", {})
        cpu_freq_payload = self._read_json(session_dir / "timeseries" / "cpu" / "cpu_frequency_timeseries.json", {})
        network_payload = self._read_json(session_dir / "timeseries" / "network" / "network_timeseries.json", {})
        network_iface_payload = self._read_json(session_dir / "timeseries" / "network" / "network_interfaces_timeseries.json", {})
        irq_payload = self._read_json(session_dir / "timeseries" / "irq" / "irq_timeseries.json", {})
        irq_top_payload = self._read_json(session_dir / "timeseries" / "irq" / "irq_top_sources.json", {})
        soft_payload = self._read_json(session_dir / "timeseries" / "softirq" / "softirq_timeseries.json", {})

        cpu_util_points = cpu_util_payload.get("samples", []) if isinstance(cpu_util_payload, dict) else []
        cpu_avg_stats = self._series_stats([self._safe_float(item.get("avg_util_percent")) for item in cpu_util_points])
        cpu_max_stats = self._series_stats([self._safe_float(item.get("max_util_percent")) for item in cpu_util_points])

        cpu_freq_points = cpu_freq_payload.get("samples", []) if isinstance(cpu_freq_payload, dict) else []
        freq_stats = self._series_stats([self._safe_float(item.get("cpu_mhz")) for item in cpu_freq_points])

        irq_points = irq_payload.get("samples", []) if isinstance(irq_payload, dict) else []
        irq_top_sources = irq_top_payload.get("top_sources", []) if isinstance(irq_top_payload, dict) else []

        network_points = network_payload.get("samples", []) if isinstance(network_payload, dict) else []
        rx_stats = self._series_stats([self._safe_float(item.get("rx_bps")) for item in network_points])
        tx_stats = self._series_stats([self._safe_float(item.get("tx_bps")) for item in network_points])
        total_rx_bytes = self._integrate_series(network_points, "rx_bps")
        total_tx_bytes = self._integrate_series(network_points, "tx_bps")

        iface_map = network_iface_payload.get("interfaces", {}) if isinstance(network_iface_payload, dict) else {}

        soft_points = soft_payload.get("samples", []) if isinstance(soft_payload, dict) else []
        soft_stats = self._series_stats([self._safe_float(item.get("total_rate")) for item in soft_points])

        sections: List[str] = []

        if irq_top_sources:
            irq_table = "".join(
                f"<tr><td>{escape(str(row.get('irq', 'N/A')))}</td><td>{escape(str(row.get('source', row.get('name', 'N/A'))))}</td><td>{self._safe_float(row.get('avg_rate')):.2f}</td><td>{escape(str(row.get('top_cpu', 'N/A')))}</td><td>{escape(str(row.get('nic', '')))}</td></tr>"
                for row in irq_top_sources
            )
            sections.append(
                f"""
                <section class="card">
                  <h3>IRQ Activity</h3>
                  <div class="summary">Top IRQ sources observed during capture and their CPU handling distribution.</div>
                  <table>
                    <thead><tr><th>IRQ</th><th>Source</th><th>Avg IRQ/s</th><th>Top CPU</th><th>NIC</th></tr></thead>
                    <tbody>{irq_table}</tbody>
                  </table>
                  <h4>IRQ Activity Over Time</h4>
                  {self._svg_line_chart(irq_points, 'irq_rate', color='#1557c0') if irq_points else '<div class="empty">No IRQ time-series data was captured.</div>'}
                </section>
                """
            )
        else:
            sections.append(self._render_no_data("IRQ Activity"))

        if network_points or iface_map:
            iface_rows_html: List[str] = []
            for iface, rows in sorted(iface_map.items(), key=lambda kv: kv[0]):
                rx_values = [self._safe_float(x.get("rx_bps")) for x in rows]
                tx_values = [self._safe_float(x.get("tx_bps")) for x in rows]
                iface_rows_html.append(
                    f"<tr><td>{escape(str(iface))}</td><td>{self._format_rate(sum(rx_values)/len(rx_values) if rx_values else 0.0)}</td><td>{self._format_rate(sum(tx_values)/len(tx_values) if tx_values else 0.0)}</td><td>{self._format_rate(max(rx_values) if rx_values else 0.0)}</td><td>{self._format_rate(max(tx_values) if tx_values else 0.0)}</td><td>{self._format_rate(rx_values[-1] if rx_values else 0.0)}</td><td>{self._format_rate(tx_values[-1] if tx_values else 0.0)}</td></tr>"
                )

            top_ifaces = sorted(
                iface_map.items(),
                key=lambda kv: max([self._safe_float(x.get("rx_bps")) + self._safe_float(x.get("tx_bps")) for x in kv[1]] or [0.0]),
                reverse=True,
            )[:3]
            iface_charts: List[str] = []
            for iface, rows in top_ifaces:
                iface_charts.append(
                    f"""
                    <div class="sub-card">
                      <h4>Interface {escape(str(iface))}</h4>
                      <div class="summary">RX over time</div>
                      {self._svg_line_chart(rows, 'rx_bps', color='#0f8a55')}
                      <div class="summary">TX over time</div>
                      {self._svg_line_chart(rows, 'tx_bps', color='#1557c0')}
                    </div>
                    """
                )

            sections.append(
                f"""
                <section class="card">
                  <h3>Network Activity</h3>
                  <div class="kpi-grid">
                    <div class="kpi"><span>Total RX</span><strong>{self._format_bytes(total_rx_bytes)}</strong></div>
                    <div class="kpi"><span>Total TX</span><strong>{self._format_bytes(total_tx_bytes)}</strong></div>
                    <div class="kpi"><span>Peak RX</span><strong>{self._format_rate(rx_stats['max'])}</strong></div>
                    <div class="kpi"><span>Peak TX</span><strong>{self._format_rate(tx_stats['max'])}</strong></div>
                    <div class="kpi"><span>Latest RX</span><strong>{self._format_rate(rx_stats['latest'])}</strong></div>
                    <div class="kpi"><span>Latest TX</span><strong>{self._format_rate(tx_stats['latest'])}</strong></div>
                  </div>
                  <h4>Network Totals Over Time</h4>
                  {self._svg_line_chart(network_points, 'rx_bps', color='#0f8a55') if network_points else '<div class="empty">No network RX time-series data was captured.</div>'}
                  {self._svg_line_chart(network_points, 'tx_bps', color='#1557c0') if network_points else ''}
                  <div class="sub-grid">{''.join(iface_charts) if iface_charts else '<div class="empty">No per-interface time-series data was captured.</div>'}</div>
                  <h4>Interface Summary</h4>
                  <table>
                    <thead><tr><th>Interface</th><th>Avg RX</th><th>Avg TX</th><th>Peak RX</th><th>Peak TX</th><th>Latest RX</th><th>Latest TX</th></tr></thead>
                    <tbody>{''.join(iface_rows_html) if iface_rows_html else '<tr><td colspan="7">No interface samples available.</td></tr>'}</tbody>
                  </table>
                </section>
                """
            )
        else:
            sections.append(self._render_no_data("Network Activity"))

        if soft_points:
            sections.append(
                f"""
                <section class="card">
                  <h3>SoftIRQ Activity</h3>
                  <div class="kpi-grid">
                    <div class="kpi"><span>Average</span><strong>{self._safe_float(soft_stats['avg']):.2f}/s</strong></div>
                    <div class="kpi"><span>Peak</span><strong>{self._safe_float(soft_stats['max']):.2f}/s</strong></div>
                    <div class="kpi"><span>Latest</span><strong>{self._safe_float(soft_stats['latest']):.2f}/s</strong></div>
                  </div>
                  {self._svg_line_chart(soft_points, 'total_rate', color='#946100')}
                </section>
                """
            )
        else:
            sections.append("<section class=\"card\"><h3>SoftIRQ Activity</h3><div class=\"empty\">No SoftIRQ time-series data was captured.</div></section>")

        artifact_rows: List[str] = []
        for category, entries in sorted(grouped.items(), key=lambda kv: kv[0]):
            for item in sorted(entries, key=lambda x: x.name):
                rel = Path(item.path)
                try:
                    rel = rel.relative_to(session_dir)
                except Exception:
                    rel = Path(item.name)
                artifact_rows.append(
                    f"<tr><td>{escape(category)}</td><td>{escape(item.name)}</td><td>{escape(item.format)}</td><td>{int(item.size_bytes)}</td><td><a href=\"{escape(rel.as_posix())}\">open</a></td></tr>"
                )
        sections.append(
            f"""
            <section class=\"card\">
              <h3>Captured Artifacts</h3>
              <table>
                <thead><tr><th>Category</th><th>File</th><th>Format</th><th>Size (bytes)</th><th>Link</th></tr></thead>
                <tbody>{''.join(artifact_rows) if artifact_rows else '<tr><td colspan="5">No files were recorded for this session.</td></tr>'}</tbody>
              </table>
            </section>
            """
        )

        html = f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>IRQLENS Session Report - {escape(session.session_id)}</title>
  <style>
    :root {{ --bg:#f4f7fb; --panel:#ffffff; --ink:#0f1b2d; --muted:#5e6b80; --line:#d8e0ee; --brand:#1557c0; --good:#1b8f47; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:Segoe UI, Arial, sans-serif; background:var(--bg); color:var(--ink); }}
    .wrap {{ max-width:1100px; margin:18px auto; padding:0 14px 20px; }}
    .hero {{ background:linear-gradient(120deg, #edf3ff, #ffffff); border:1px solid var(--line); border-radius:12px; padding:14px; }}
    h1 {{ margin:0 0 4px; font-size:22px; }}
    h3 {{ margin:0 0 8px; font-size:18px; }}
    h4 {{ margin:12px 0 8px; font-size:14px; color:var(--muted); }}
    .meta {{ color:var(--muted); font-size:13px; }}
    .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(190px,1fr)); gap:8px; margin-top:12px; }}
    .kpi-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:8px; margin:10px 0; }}
    .kpi {{ background:#fff; border:1px solid var(--line); border-radius:8px; padding:8px; }}
    .kpi span {{ display:block; color:var(--muted); font-size:12px; margin-bottom:2px; }}
    .kpi strong {{ font-size:15px; }}
    .card {{ background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:12px; margin-top:10px; }}
    .sub-card {{ border:1px solid var(--line); border-radius:10px; padding:10px; background:#f9fbff; }}
    .sub-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:8px; margin-top:8px; }}
    .summary {{ color:var(--muted); font-size:13px; margin-bottom:8px; }}
    .empty {{ color:var(--muted); font-size:13px; border:1px dashed var(--line); border-radius:8px; padding:10px; background:#f8fbff; }}
    .chart-svg {{ width:100%; height:auto; display:block; border:1px solid #e8eef8; border-radius:8px; background:#fff; }}
    table {{ width:100%; border-collapse:collapse; font-size:13px; }}
    th, td {{ border-bottom:1px solid var(--line); padding:7px; text-align:left; }}
    th {{ color:var(--muted); font-weight:600; }}
    a {{ color:var(--brand); text-decoration:none; }}
  </style>
</head>
<body>
  <div class=\"wrap\">
    <div class=\"hero\">
      <h1>IRQLENS Session Report</h1>
            <div class="meta">Session {escape(session.session_id)} - Host {escape(session.hostname)} - SUT {escape(session.sut_id or 'local')}</div>
      <div class=\"grid\">
        <div class=\"kpi\"><span>Status</span><strong>{escape(session.status)}</strong></div>
        <div class=\"kpi\"><span>Start</span><strong>{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(session.start_time))}</strong></div>
        <div class=\"kpi\"><span>End</span><strong>{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(session.end_time)) if session.end_time else 'N/A'}</strong></div>
        <div class=\"kpi\"><span>Duration</span><strong>{duration}s</strong></div>
        <div class=\"kpi\"><span>OS</span><strong>{escape(session.os_distribution)}</strong></div>
        <div class=\"kpi\"><span>Kernel</span><strong>{escape(session.kernel)}</strong></div>
        <div class=\"kpi\"><span>CPU Count</span><strong>{int(system_payload.get('cpu_count', 0)) if isinstance(system_payload, dict) else 0}</strong></div>
                <div class="kpi"><span>CPU Util Avg</span><strong>{f"{self._safe_float(cpu_avg_stats['avg']):.2f}%" if cpu_avg_stats['avg'] is not None else 'Not captured'}</strong></div>
                <div class="kpi"><span>CPU Util Peak</span><strong>{f"{self._safe_float(cpu_max_stats['max']):.2f}%" if cpu_max_stats['max'] is not None else 'Not captured'}</strong></div>
                <div class="kpi"><span>CPU Frequency Avg</span><strong>{f"{(self._safe_float(freq_stats['avg'])/1000.0):.2f} GHz" if freq_stats['avg'] is not None else 'Not captured'}</strong></div>
                <div class="kpi"><span>Network Total RX</span><strong>{self._format_bytes(total_rx_bytes)}</strong></div>
                <div class="kpi"><span>Network Total TX</span><strong>{self._format_bytes(total_tx_bytes)}</strong></div>
                <div class="kpi"><span>Top IRQ</span><strong>{escape(str((irq_top_sources[0].get('source') if irq_top_sources else 'Not captured')))}</strong></div>
      </div>
    </div>
        <section class="card">
            <h3>CPU Performance</h3>
            <div class="kpi-grid">
                <div class="kpi"><span>CPU Utilization Minimum</span><strong>{f"{self._safe_float(cpu_avg_stats['min']):.2f}%" if cpu_avg_stats['min'] is not None else 'Not captured'}</strong></div>
                <div class="kpi"><span>CPU Utilization Latest</span><strong>{f"{self._safe_float(cpu_avg_stats['latest']):.2f}%" if cpu_avg_stats['latest'] is not None else 'Not captured'}</strong></div>
                <div class="kpi"><span>CPU Frequency Minimum</span><strong>{f"{(self._safe_float(freq_stats['min'])/1000.0):.2f} GHz" if freq_stats['min'] is not None else 'Not captured'}</strong></div>
                <div class="kpi"><span>CPU Frequency Peak</span><strong>{f"{(self._safe_float(freq_stats['max'])/1000.0):.2f} GHz" if freq_stats['max'] is not None else 'Not captured'}</strong></div>
                <div class="kpi"><span>CPU Frequency Latest</span><strong>{f"{(self._safe_float(freq_stats['latest'])/1000.0):.2f} GHz" if freq_stats['latest'] is not None else 'Not captured'}</strong></div>
            </div>
            <h4>CPU Utilization Over Capture Duration</h4>
            {self._svg_line_chart(cpu_util_points, 'avg_util_percent', color='#1557c0') if cpu_util_points else '<div class="empty">No CPU utilization time-series data was captured.</div>'}
            <h4>CPU Frequency Over Capture Duration</h4>
            {self._svg_line_chart(cpu_freq_points, 'cpu_mhz', color='#0f8a55') if cpu_freq_points else '<div class="empty">CPU Frequency Not captured.</div>'}
        </section>
    {''.join(sections)}
  </div>
</body>
</html>
"""

        report_path = session_dir / "report.html"
        report_path.write_text(html, encoding="utf-8")
        self.store.add_session_files(
            session_id,
            [
                ExportFile(
                    name=report_path.name,
                    category="report",
                    format="html",
                    path=str(report_path),
                    size_bytes=report_path.stat().st_size,
                )
            ],
        )
        self.store.update_session_report(session_id, report_status="ready", report_path=str(report_path), report_error="")
        return report_path
