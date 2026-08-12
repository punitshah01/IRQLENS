from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path
from typing import List, Optional, Tuple

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
                    safe_name = "_".join(token.replace("/", "_") for token in cmd)
                    raw_path = command_dir / f"{safe_name}.txt"
                    raw_path.parent.mkdir(parents=True, exist_ok=True)
                    raw_path.write_text(
                        f"command: {' '.join(cmd)}\n"
                        f"timestamp: {result.timestamp}\n"
                        f"exit_code: {result.exit_code}\n\n"
                        f"stdout:\n{result.stdout}\n\n"
                        f"stderr:\n{result.stderr}\n",
                        encoding="utf-8",
                    )
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

        xml_path = category_dir / f"{category}.xml"
        self.exporter.write_xml(xml_path, "irqlens", category, rows)
        out.append(self._to_export_file(xml_path, category, "xml"))

        txt_path = category_dir / f"{category}.txt"
        self.exporter.write_txt(txt_path, f"IRQLENS {category} diagnostics", [str(row) for row in rows])
        out.append(self._to_export_file(txt_path, category, "txt"))

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
