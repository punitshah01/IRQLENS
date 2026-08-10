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

    def _session_dir(self, session_id: str) -> Path:
        return self.settings.output_dir / "sessions" / self._safe_session_id(session_id)

    def start(self, categories: List[str], system_hostname: str, os_distribution: str, kernel: str) -> CollectionSession:
        ts = time.time()
        session_id = time.strftime("%Y%m%d-%H%M%S", time.localtime(ts))
        session_id = f"{session_id}-{int((ts % 1) * 1000):03d}"
        outdir = self._session_dir(session_id)
        outdir.mkdir(parents=True, exist_ok=True)

        session = CollectionSession(
            session_id=session_id,
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

        metadata_path = outdir / "metadata.json"
        self.exporter.write_json(metadata_path, session.model_dump())
        return session

    def collect_snapshot(self, session_id: str, categories: List[str]) -> List[ExportFile]:
        outdir = self._session_dir(session_id)
        outdir.mkdir(parents=True, exist_ok=True)

        interfaces = self.network_collector.discover_interfaces()
        system = self.system_collector.collect(time.time())
        ts = time.time()

        net_rows_raw, net_global, iface_infos = self.network_collector.collect(1.0, ts)
        files: List[ExportFile] = []
        hosts = self.store.hosts()
        selected_host = hosts[0] if hosts else "local"

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

        if "network" in categories or "interfaces" in categories:
            category_dir = outdir / "network"
            network_json = {
                "timestamp": ts,
                "global": net_global,
                "interfaces": iface_infos,
                "samples": net_rows_raw,
            }
            files.extend(self._emit_category_files(category_dir, "network", network_json, net_rows_raw))

        if "system" in categories:
            category_dir = outdir / "system"
            files.extend(self._emit_category_files(category_dir, "system", system, [system]))

        command_categories = [c for c in categories if c in {"network", "interfaces", "routes", "sockets", "ethtool"}]
        if command_categories:
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
        self.store.update_session_status(session_id, status="stopped", end_time=time.time(), error="" if reason == "manual" else reason)
        if self._active_session_id == session_id:
            self._active_session_id = ""
        return self.store.get_session(session_id)

    def list_files(self, session_id: str) -> List[ExportFile]:
        return self.store.session_files(session_id)

    def archive_session(self, session_id: str) -> Optional[Path]:
        session = self.store.get_session(session_id)
        if not session:
            return None
        session_dir = Path(session.output_dir)
        if not session_dir.exists():
            return None
        archive_base = self.settings.output_dir / "sessions" / f"{session_id}"
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
