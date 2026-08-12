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
        irq_payload = self._read_json(session_dir / "irq" / "irqtop.json", {})
        soft_payload = self._read_json(session_dir / "softirq" / "softirq.json", {})
        network_payload = self._read_json(session_dir / "network" / "network.json", {})
        interfaces_payload = self._read_json(session_dir / "interfaces" / "interfaces.json", {})

        cpu_util = system_payload.get("cpu_utilization", {}) if isinstance(system_payload, dict) else {}
        cpu_util_values = []
        if isinstance(cpu_util, dict):
            for value in cpu_util.values():
                try:
                    cpu_util_values.append(float(value))
                except Exception:
                    continue
        cpu_util_avg = round(sum(cpu_util_values) / len(cpu_util_values), 2) if cpu_util_values else None
        cpu_util_max = round(max(cpu_util_values), 2) if cpu_util_values else None

        irq_rows = irq_payload.get("rows", []) if isinstance(irq_payload, dict) else []
        top_irq_rows = sorted(irq_rows, key=lambda row: float(row.get("total_rate", 0.0)), reverse=True)[:10]

        network_global = network_payload.get("global", {}) if isinstance(network_payload, dict) else {}
        interface_rows = network_payload.get("interfaces", []) if isinstance(network_payload, dict) else []
        if not interface_rows and isinstance(interfaces_payload, dict):
            interface_rows = interfaces_payload.get("interfaces", []) or []

        soft_rates = {}
        if isinstance(soft_payload, dict) and isinstance(soft_payload.get("sample"), dict):
            soft_rates = soft_payload["sample"].get("rates", {}) or {}
        soft_total = 0.0
        for value in soft_rates.values() if isinstance(soft_rates, dict) else []:
            try:
                soft_total += float(value)
            except Exception:
                continue

        sections: List[str] = []

        if irq_rows:
            irq_table = "".join(
                f"<tr><td>{escape(str(row.get('irq', 'N/A')))}</td><td>{escape(str(row.get('irq_name', 'N/A')))}</td><td>{float(row.get('total_rate', 0.0)):.2f}</td><td>{escape(str(row.get('nic', 'N/A')))}</td></tr>"
                for row in top_irq_rows
            )
            sections.append(
                f"""
                <section class=\"card\">
                  <h3>IRQ Activity</h3>
                  <div class=\"summary\">Top IRQ sources and per-source activity at capture time.</div>
                  <table>
                    <thead><tr><th>IRQ</th><th>Source</th><th>IRQ/s</th><th>Interface</th></tr></thead>
                    <tbody>{irq_table}</tbody>
                  </table>
                </section>
                """
            )
        else:
            sections.append(self._render_no_data("IRQ Activity"))

        if network_global or interface_rows:
            iface_table = "".join(
                f"<tr><td>{escape(str(row.get('interface', row.get('name', 'N/A'))))}</td><td>{float(row.get('rx_bps', 0.0)):.2f}</td><td>{float(row.get('tx_bps', 0.0)):.2f}</td><td>{float(row.get('rx_pps', 0.0)):.2f}</td><td>{float(row.get('tx_pps', 0.0)):.2f}</td><td>{float(row.get('rx_err_ps', 0.0) + row.get('tx_err_ps', 0.0)):.2f}</td><td>{float(row.get('rx_drop_ps', 0.0) + row.get('tx_drop_ps', 0.0)):.2f}</td></tr>"
                for row in interface_rows
            )
            sections.append(
                f"""
                <section class=\"card\">
                  <h3>Network Activity</h3>
                  <div class=\"kpi-grid\">
                    <div class=\"kpi\"><span>RX</span><strong>{float(network_global.get('rx_bps', 0.0)):.2f} B/s</strong></div>
                    <div class=\"kpi\"><span>TX</span><strong>{float(network_global.get('tx_bps', 0.0)):.2f} B/s</strong></div>
                    <div class=\"kpi\"><span>Errors</span><strong>{float(network_global.get('rx_err_ps', 0.0) + network_global.get('tx_err_ps', 0.0)):.2f}/s</strong></div>
                    <div class=\"kpi\"><span>Drops</span><strong>{float(network_global.get('rx_drop_ps', 0.0) + network_global.get('tx_drop_ps', 0.0)):.2f}/s</strong></div>
                  </div>
                  <h4>Interfaces</h4>
                  <table>
                    <thead><tr><th>Interface</th><th>RX B/s</th><th>TX B/s</th><th>RX pps</th><th>TX pps</th><th>Errors/s</th><th>Drops/s</th></tr></thead>
                    <tbody>{iface_table or '<tr><td colspan="7">No interface samples available.</td></tr>'}</tbody>
                  </table>
                </section>
                """
            )
        else:
            sections.append(self._render_no_data("Network Activity"))

        if soft_rates:
            soft_table = "".join(
                f"<tr><td>{escape(str(name))}</td><td>{float(value):.2f}</td></tr>"
                for name, value in sorted(soft_rates.items(), key=lambda kv: float(kv[1]), reverse=True)
            )
            sections.append(
                f"""
                <section class=\"card\">
                  <h3>SoftIRQ Activity</h3>
                  <div class=\"summary\">Total SoftIRQ/s: <strong>{soft_total:.2f}</strong></div>
                  <table>
                    <thead><tr><th>Class</th><th>Rate/s</th></tr></thead>
                    <tbody>{soft_table}</tbody>
                  </table>
                </section>
                """
            )
        else:
            sections.append(self._render_no_data("SoftIRQ Activity"))

        artifact_rows: List[str] = []
        for category, entries in sorted(grouped.items(), key=lambda kv: kv[0]):
            for item in sorted(entries, key=lambda x: x.name):
                artifact_rows.append(
                    f"<tr><td>{escape(category)}</td><td>{escape(item.name)}</td><td>{escape(item.format)}</td><td>{int(item.size_bytes)}</td><td><a href=\"/api/files?path={quote(item.path, safe='')}\">open</a></td></tr>"
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
    .summary {{ color:var(--muted); font-size:13px; margin-bottom:8px; }}
    .empty {{ color:var(--muted); font-size:13px; border:1px dashed var(--line); border-radius:8px; padding:10px; background:#f8fbff; }}
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
      <div class=\"meta\">Session {escape(session.session_id)} • Host {escape(session.hostname)} • SUT {escape(session.sut_id or 'local')}</div>
      <div class=\"grid\">
        <div class=\"kpi\"><span>Status</span><strong>{escape(session.status)}</strong></div>
        <div class=\"kpi\"><span>Start</span><strong>{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(session.start_time))}</strong></div>
        <div class=\"kpi\"><span>End</span><strong>{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(session.end_time)) if session.end_time else 'N/A'}</strong></div>
        <div class=\"kpi\"><span>Duration</span><strong>{duration}s</strong></div>
        <div class=\"kpi\"><span>OS</span><strong>{escape(session.os_distribution)}</strong></div>
        <div class=\"kpi\"><span>Kernel</span><strong>{escape(session.kernel)}</strong></div>
        <div class=\"kpi\"><span>CPU Count</span><strong>{int(system_payload.get('cpu_count', 0)) if isinstance(system_payload, dict) else 0}</strong></div>
        <div class=\"kpi\"><span>CPU Util Avg</span><strong>{f'{cpu_util_avg:.2f}%' if cpu_util_avg is not None else 'N/A'}</strong></div>
        <div class=\"kpi\"><span>CPU Util Max</span><strong>{f'{cpu_util_max:.2f}%' if cpu_util_max is not None else 'N/A'}</strong></div>
        <div class=\"kpi\"><span>CPU Frequency</span><strong>{escape(str(system_payload.get('cpu_mhz', 'N/A'))) if isinstance(system_payload, dict) else 'N/A'}</strong></div>
      </div>
    </div>
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
