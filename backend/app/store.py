from __future__ import annotations

import json
import sqlite3
import time
from threading import Lock
from typing import Any, Dict, List, Optional

from .config import settings
from .models import CollectionSession, ExportFile, InterfaceInfo, IRQSample, NetworkSample, SoftIRQSample, SystemInfo, SystemRecord


class SqliteStore:
    def __init__(self, db_path: str, retention_rows: int) -> None:
        self._db_path = db_path
        self._retention_rows = retention_rows
        self._lock = Lock()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS irq_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    sut_ip TEXT NOT NULL,
                    irq TEXT NOT NULL,
                    irq_name TEXT NOT NULL,
                    device TEXT NOT NULL,
                    interrupt_type TEXT NOT NULL,
                    affinity_list TEXT NOT NULL,
                    numa_node TEXT NOT NULL,
                    nic TEXT NOT NULL,
                    queue TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    source_class TEXT NOT NULL,
                    total_count INTEGER NOT NULL,
                    total_rate REAL NOT NULL,
                    cpu_rates_json TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_irq_host_ts ON irq_samples(sut_ip, timestamp)")
            self._ensure_column(conn, "irq_samples", "device", "TEXT NOT NULL DEFAULT 'N/A'")
            self._ensure_column(conn, "irq_samples", "interrupt_type", "TEXT NOT NULL DEFAULT 'N/A'")
            self._ensure_column(conn, "irq_samples", "numa_node", "TEXT NOT NULL DEFAULT 'N/A'")
            self._ensure_column(conn, "irq_samples", "total_count", "INTEGER NOT NULL DEFAULT 0")

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS softirq_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    sut_ip TEXT NOT NULL,
                    totals_json TEXT NOT NULL,
                    rates_json TEXT NOT NULL,
                    per_cpu_rates_json TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_softirq_host_ts ON softirq_samples(sut_ip, timestamp)")

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS network_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    sut_ip TEXT NOT NULL,
                    interface TEXT NOT NULL,
                    rx_bytes INTEGER NOT NULL,
                    tx_bytes INTEGER NOT NULL,
                    rx_packets INTEGER NOT NULL,
                    tx_packets INTEGER NOT NULL,
                    rx_errors INTEGER NOT NULL,
                    tx_errors INTEGER NOT NULL,
                    rx_drops INTEGER NOT NULL,
                    tx_drops INTEGER NOT NULL,
                    rx_bps REAL NOT NULL,
                    tx_bps REAL NOT NULL,
                    rx_pps REAL NOT NULL,
                    tx_pps REAL NOT NULL,
                    rx_err_ps REAL NOT NULL,
                    tx_err_ps REAL NOT NULL,
                    rx_drop_ps REAL NOT NULL,
                    tx_drop_ps REAL NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_network_host_ts ON network_samples(sut_ip, timestamp)")

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS interface_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    sut_ip TEXT NOT NULL,
                    interface TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_interface_host_ts ON interface_samples(sut_ip, timestamp)")

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS system_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    sut_ip TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_system_host_ts ON system_samples(sut_ip, timestamp)")

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    start_time REAL NOT NULL,
                    end_time REAL,
                    hostname TEXT NOT NULL,
                    os_distribution TEXT NOT NULL,
                    kernel TEXT NOT NULL,
                    collector_version TEXT NOT NULL,
                    output_dir TEXT NOT NULL,
                    categories_json TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_start ON sessions(start_time DESC)")
            self._ensure_column(conn, "sessions", "sut_id", "TEXT NOT NULL DEFAULT ''")

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS session_files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    format TEXT NOT NULL,
                    path TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_session_files_session ON session_files(session_id)")

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS systems (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    hostname TEXT NOT NULL,
                    address TEXT NOT NULL,
                    port INTEGER NOT NULL,
                    os_distribution TEXT NOT NULL,
                    os_version TEXT NOT NULL,
                    kernel TEXT NOT NULL,
                    architecture TEXT NOT NULL,
                    agent_version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    last_seen REAL NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    cpu_count INTEGER NOT NULL DEFAULT 0,
                    cpu_model TEXT NOT NULL DEFAULT '',
                    memory_total_kb INTEGER NOT NULL DEFAULT 0,
                    numa_nodes INTEGER NOT NULL DEFAULT 0,
                    interfaces_json TEXT NOT NULL DEFAULT '[]',
                    ip_addresses_json TEXT NOT NULL DEFAULT '[]',
                    mode TEXT NOT NULL DEFAULT 'remote'
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_systems_status ON systems(status, updated_at)")

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_heartbeats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sut_id TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    uptime_seconds REAL NOT NULL,
                    agent_version TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_heartbeats_sut_ts ON agent_heartbeats(sut_id, timestamp)")

    def _ensure_column(self, conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        existing = {row[1] for row in rows}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def _trim_table(self, conn: sqlite3.Connection, table: str, host: str) -> None:
        conn.execute(
            f"""
            DELETE FROM {table}
            WHERE sut_ip = ? AND id NOT IN (
                SELECT id FROM {table}
                WHERE sut_ip = ?
                ORDER BY id DESC
                LIMIT ?
            )
            """,
            (host, host, self._retention_rows),
        )

    def add_irq_samples(self, samples: List[IRQSample]) -> None:
        if not samples:
            return
        with self._lock:
            with self._conn() as conn:
                hosts = set()
                for sample in samples:
                    hosts.add(sample.sut_ip)
                    conn.execute(
                        """
                        INSERT INTO irq_samples(
                            timestamp, sut_ip, irq, irq_name, device, interrupt_type,
                            affinity_list, numa_node, nic, queue, direction, source_class,
                            total_count, total_rate, cpu_rates_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            sample.timestamp,
                            sample.sut_ip,
                            sample.irq,
                            sample.irq_name,
                            sample.device,
                            sample.interrupt_type,
                            sample.affinity_list,
                            sample.numa_node,
                            sample.nic,
                            sample.queue,
                            sample.direction,
                            sample.source_class,
                            sample.total_count,
                            sample.total_rate,
                            json.dumps(sample.cpu_rates, separators=(",", ":")),
                        ),
                    )
                for host in hosts:
                    self._trim_table(conn, "irq_samples", host)

    def add_softirq_sample(self, sample: SoftIRQSample) -> None:
        with self._lock:
            with self._conn() as conn:
                conn.execute(
                    """
                    INSERT INTO softirq_samples(timestamp, sut_ip, totals_json, rates_json, per_cpu_rates_json)
                    VALUES(?, ?, ?, ?, ?)
                    """,
                    (
                        sample.timestamp,
                        sample.sut_ip,
                        json.dumps(sample.totals, separators=(",", ":")),
                        json.dumps(sample.rates, separators=(",", ":")),
                        json.dumps(sample.per_cpu_rates, separators=(",", ":")),
                    ),
                )
                self._trim_table(conn, "softirq_samples", sample.sut_ip)

    def add_network_samples(self, samples: List[NetworkSample]) -> None:
        if not samples:
            return
        with self._lock:
            with self._conn() as conn:
                hosts = set()
                for sample in samples:
                    hosts.add(sample.sut_ip)
                    conn.execute(
                        """
                        INSERT INTO network_samples(
                            timestamp, sut_ip, interface,
                            rx_bytes, tx_bytes, rx_packets, tx_packets,
                            rx_errors, tx_errors, rx_drops, tx_drops,
                            rx_bps, tx_bps, rx_pps, tx_pps,
                            rx_err_ps, tx_err_ps, rx_drop_ps, tx_drop_ps
                        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            sample.timestamp,
                            sample.sut_ip,
                            sample.interface,
                            sample.rx_bytes,
                            sample.tx_bytes,
                            sample.rx_packets,
                            sample.tx_packets,
                            sample.rx_errors,
                            sample.tx_errors,
                            sample.rx_drops,
                            sample.tx_drops,
                            sample.rx_bps,
                            sample.tx_bps,
                            sample.rx_pps,
                            sample.tx_pps,
                            sample.rx_err_ps,
                            sample.tx_err_ps,
                            sample.rx_drop_ps,
                            sample.tx_drop_ps,
                        ),
                    )
                for host in hosts:
                    self._trim_table(conn, "network_samples", host)

    def add_interfaces(self, sut_ip: str, interfaces: List[InterfaceInfo]) -> None:
        if not interfaces:
            return
        with self._lock:
            with self._conn() as conn:
                ts = time.time()
                for info in interfaces:
                    conn.execute(
                        """
                        INSERT INTO interface_samples(timestamp, sut_ip, interface, payload_json)
                        VALUES(?, ?, ?, ?)
                        """,
                        (ts, sut_ip, info.name, info.model_dump_json()),
                    )
                self._trim_table(conn, "interface_samples", sut_ip)

    def add_system(self, sut_ip: str, system: SystemInfo) -> None:
        with self._lock:
            with self._conn() as conn:
                conn.execute(
                    """
                    INSERT INTO system_samples(timestamp, sut_ip, payload_json)
                    VALUES(?, ?, ?)
                    """,
                    (system.timestamp, sut_ip, system.model_dump_json()),
                )
                self._trim_table(conn, "system_samples", sut_ip)

    def hosts(self) -> List[str]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT sut_ip FROM irq_samples
                UNION
                SELECT sut_ip FROM network_samples
                UNION
                SELECT sut_ip FROM system_samples
                ORDER BY sut_ip
                """
            ).fetchall()
        return [row[0] for row in rows]

    def latest_irq(self, sut_ip: str, limit: int = 500) -> List[IRQSample]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM irq_samples
                WHERE sut_ip = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (sut_ip, limit),
            ).fetchall()
        out: List[IRQSample] = []
        for row in reversed(rows):
            out.append(
                IRQSample(
                    timestamp=row["timestamp"],
                    sut_ip=row["sut_ip"],
                    irq=row["irq"],
                    irq_name=row["irq_name"],
                    device=row["device"],
                    interrupt_type=row["interrupt_type"],
                    affinity_list=row["affinity_list"],
                    numa_node=row["numa_node"],
                    nic=row["nic"],
                    queue=row["queue"],
                    direction=row["direction"],
                    source_class=row["source_class"],
                    total_count=row["total_count"],
                    total_rate=row["total_rate"],
                    cpu_rates=json.loads(row["cpu_rates_json"] or "{}"),
                )
            )
        return out

    def latest_irq_timestamp(self, sut_ip: str) -> Optional[float]:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT MAX(timestamp) AS ts
                FROM irq_samples
                WHERE sut_ip = ?
                """,
                (sut_ip,),
            ).fetchone()
        if not row or row["ts"] is None:
            return None
        return float(row["ts"])

    def irq_at_timestamp(self, sut_ip: str, timestamp: float, limit: int = 2000) -> List[IRQSample]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM irq_samples
                WHERE sut_ip = ? AND timestamp = ?
                ORDER BY total_rate DESC
                LIMIT ?
                """,
                (sut_ip, timestamp, limit),
            ).fetchall()
        out: List[IRQSample] = []
        for row in rows:
            out.append(
                IRQSample(
                    timestamp=row["timestamp"],
                    sut_ip=row["sut_ip"],
                    irq=row["irq"],
                    irq_name=row["irq_name"],
                    device=row["device"],
                    interrupt_type=row["interrupt_type"],
                    affinity_list=row["affinity_list"],
                    numa_node=row["numa_node"],
                    nic=row["nic"],
                    queue=row["queue"],
                    direction=row["direction"],
                    source_class=row["source_class"],
                    total_count=row["total_count"],
                    total_rate=row["total_rate"],
                    cpu_rates=json.loads(row["cpu_rates_json"] or "{}"),
                )
            )
        return out

    def latest_softirq(self, sut_ip: str) -> Optional[SoftIRQSample]:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT * FROM softirq_samples
                WHERE sut_ip = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (sut_ip,),
            ).fetchone()
        if not row:
            return None
        return SoftIRQSample(
            timestamp=row["timestamp"],
            sut_ip=row["sut_ip"],
            totals=json.loads(row["totals_json"] or "{}"),
            rates=json.loads(row["rates_json"] or "{}"),
            per_cpu_rates=json.loads(row["per_cpu_rates_json"] or "{}"),
        )

    def latest_network(self, sut_ip: str, limit: int = 2000) -> List[NetworkSample]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM network_samples
                WHERE sut_ip = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (sut_ip, limit),
            ).fetchall()
        out: List[NetworkSample] = []
        for row in reversed(rows):
            out.append(
                NetworkSample(
                    timestamp=row["timestamp"],
                    sut_ip=row["sut_ip"],
                    interface=row["interface"],
                    rx_bytes=row["rx_bytes"],
                    tx_bytes=row["tx_bytes"],
                    rx_packets=row["rx_packets"],
                    tx_packets=row["tx_packets"],
                    rx_errors=row["rx_errors"],
                    tx_errors=row["tx_errors"],
                    rx_drops=row["rx_drops"],
                    tx_drops=row["tx_drops"],
                    rx_bps=row["rx_bps"],
                    tx_bps=row["tx_bps"],
                    rx_pps=row["rx_pps"],
                    tx_pps=row["tx_pps"],
                    rx_err_ps=row["rx_err_ps"],
                    tx_err_ps=row["tx_err_ps"],
                    rx_drop_ps=row["rx_drop_ps"],
                    tx_drop_ps=row["tx_drop_ps"],
                )
            )
        return out

    def irq_rate_series(self, sut_ip: str, since_ts: float) -> List[Dict[str, float]]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT timestamp, SUM(total_rate) AS irq_rate
                FROM irq_samples
                WHERE sut_ip = ? AND timestamp >= ?
                GROUP BY timestamp
                ORDER BY timestamp ASC
                """,
                (sut_ip, since_ts),
            ).fetchall()
        return [{"timestamp": float(r["timestamp"]), "irq_rate": float(r["irq_rate"] or 0.0)} for r in rows]

    def network_rate_series(self, sut_ip: str, since_ts: float) -> List[Dict[str, float]]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT timestamp,
                       SUM(rx_bps) AS rx_bps,
                       SUM(tx_bps) AS tx_bps,
                       SUM(rx_pps) AS rx_pps,
                       SUM(tx_pps) AS tx_pps,
                       SUM(rx_err_ps) AS rx_err_ps,
                       SUM(tx_err_ps) AS tx_err_ps,
                       SUM(rx_drop_ps) AS rx_drop_ps,
                       SUM(tx_drop_ps) AS tx_drop_ps
                FROM network_samples
                WHERE sut_ip = ? AND timestamp >= ?
                GROUP BY timestamp
                ORDER BY timestamp ASC
                """,
                (sut_ip, since_ts),
            ).fetchall()
        out: List[Dict[str, float]] = []
        for row in rows:
            out.append(
                {
                    "timestamp": float(row["timestamp"]),
                    "rx_bps": float(row["rx_bps"] or 0.0),
                    "tx_bps": float(row["tx_bps"] or 0.0),
                    "rx_pps": float(row["rx_pps"] or 0.0),
                    "tx_pps": float(row["tx_pps"] or 0.0),
                    "rx_err_ps": float(row["rx_err_ps"] or 0.0),
                    "tx_err_ps": float(row["tx_err_ps"] or 0.0),
                    "rx_drop_ps": float(row["rx_drop_ps"] or 0.0),
                    "tx_drop_ps": float(row["tx_drop_ps"] or 0.0),
                }
            )
        return out

    def softirq_series(self, sut_ip: str, since_ts: float) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT timestamp, rates_json, per_cpu_rates_json
                FROM softirq_samples
                WHERE sut_ip = ? AND timestamp >= ?
                ORDER BY timestamp ASC
                """,
                (sut_ip, since_ts),
            ).fetchall()
        out: List[Dict[str, Any]] = []
        for row in rows:
            rates = json.loads(row["rates_json"] or "{}")
            per_cpu = json.loads(row["per_cpu_rates_json"] or "{}")
            out.append(
                {
                    "timestamp": float(row["timestamp"]),
                    "rates": {k: float(v) for k, v in rates.items()},
                    "per_cpu_rates": {str(k): float(v) for k, v in per_cpu.items()},
                }
            )
        return out

    def latest_interfaces(self, sut_ip: str) -> List[InterfaceInfo]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT interface, MAX(id) AS max_id
                FROM interface_samples
                WHERE sut_ip = ?
                GROUP BY interface
                """,
                (sut_ip,),
            ).fetchall()
            ids = [row["max_id"] for row in rows if row["max_id"] is not None]
            if not ids:
                return []
            placeholders = ",".join(["?"] * len(ids))
            payload_rows = conn.execute(
                f"SELECT payload_json FROM interface_samples WHERE id IN ({placeholders})",
                ids,
            ).fetchall()
        out: List[InterfaceInfo] = []
        for row in payload_rows:
            out.append(InterfaceInfo.model_validate_json(row["payload_json"]))
        out.sort(key=lambda item: item.name)
        return out

    def latest_system(self, sut_ip: str) -> Optional[SystemInfo]:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT payload_json
                FROM system_samples
                WHERE sut_ip = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (sut_ip,),
            ).fetchone()
        if not row:
            return None
        return SystemInfo.model_validate_json(row["payload_json"])

    def create_session(self, session: CollectionSession) -> None:
        with self._lock:
            with self._conn() as conn:
                conn.execute(
                    """
                    INSERT INTO sessions(
                        session_id, sut_id, status, start_time, end_time, hostname, os_distribution,
                        kernel, collector_version, output_dir, categories_json, error
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session.session_id,
                        session.sut_id,
                        session.status,
                        session.start_time,
                        session.end_time,
                        session.hostname,
                        session.os_distribution,
                        session.kernel,
                        session.collector_version,
                        session.output_dir,
                        json.dumps(session.categories, separators=(",", ":")),
                        session.error,
                    ),
                )

    def update_session_status(self, session_id: str, status: str, end_time: Optional[float] = None, error: str = "") -> None:
        with self._lock:
            with self._conn() as conn:
                conn.execute(
                    """
                    UPDATE sessions
                    SET status = ?, end_time = COALESCE(?, end_time), error = ?
                    WHERE session_id = ?
                    """,
                    (status, end_time, error, session_id),
                )

    def add_session_files(self, session_id: str, files: List[ExportFile]) -> None:
        if not files:
            return
        with self._lock:
            with self._conn() as conn:
                for item in files:
                    conn.execute(
                        """
                        INSERT INTO session_files(session_id, name, category, format, path, size_bytes, created_at)
                        VALUES(?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            session_id,
                            item.name,
                            item.category,
                            item.format,
                            item.path,
                            item.size_bytes,
                            time.time(),
                        ),
                    )

    def list_sessions(self) -> List[CollectionSession]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM sessions ORDER BY start_time DESC").fetchall()
        out: List[CollectionSession] = []
        for row in rows:
            out.append(
                CollectionSession(
                    session_id=row["session_id"],
                    sut_id=row["sut_id"] or "",
                    status=row["status"],
                    start_time=row["start_time"],
                    end_time=row["end_time"],
                    hostname=row["hostname"],
                    os_distribution=row["os_distribution"],
                    kernel=row["kernel"],
                    collector_version=row["collector_version"],
                    output_dir=row["output_dir"],
                    categories=json.loads(row["categories_json"] or "[]"),
                    error=row["error"] or "",
                )
            )
        return out

    def get_session(self, session_id: str) -> Optional[CollectionSession]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
        if not row:
            return None
        return CollectionSession(
            session_id=row["session_id"],
            sut_id=row["sut_id"] or "",
            status=row["status"],
            start_time=row["start_time"],
            end_time=row["end_time"],
            hostname=row["hostname"],
            os_distribution=row["os_distribution"],
            kernel=row["kernel"],
            collector_version=row["collector_version"],
            output_dir=row["output_dir"],
            categories=json.loads(row["categories_json"] or "[]"),
            error=row["error"] or "",
        )

    def session_files(self, session_id: str) -> List[ExportFile]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT name, category, format, path, size_bytes FROM session_files WHERE session_id = ? ORDER BY id",
                (session_id,),
            ).fetchall()
        return [
            ExportFile(
                name=row["name"],
                category=row["category"],
                format=row["format"],
                path=row["path"],
                size_bytes=row["size_bytes"],
            )
            for row in rows
        ]

    def summary_current(self) -> List[Dict[str, Any]]:
        hosts = self.hosts()
        out: List[Dict[str, Any]] = []
        with self._conn() as conn:
            for host in hosts:
                net = conn.execute(
                    """
                    SELECT timestamp,
                           SUM(rx_bps) AS rx_bps,
                           SUM(tx_bps) AS tx_bps,
                           SUM(rx_pps) AS rx_pps,
                           SUM(tx_pps) AS tx_pps,
                           SUM(rx_drop_ps) AS rx_drop_ps,
                           SUM(tx_drop_ps) AS tx_drop_ps
                    FROM network_samples
                    WHERE sut_ip = ?
                    GROUP BY timestamp
                    ORDER BY timestamp DESC
                    LIMIT 1
                    """,
                    (host,),
                ).fetchone()
                soft = conn.execute(
                    """
                    SELECT rates_json
                    FROM softirq_samples
                    WHERE sut_ip = ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (host,),
                ).fetchone()
                top_irq = conn.execute(
                    """
                    SELECT irq_name, total_rate
                    FROM irq_samples
                    WHERE sut_ip = ?
                    ORDER BY timestamp DESC, total_rate DESC
                    LIMIT 1
                    """,
                    (host,),
                ).fetchone()
                if not net:
                    continue
                soft_total = 0.0
                if soft:
                    soft_rates = json.loads(soft["rates_json"] or "{}")
                    soft_total = float(sum(float(v) for v in soft_rates.values()))
                out.append(
                    {
                        "sut_ip": host,
                        "timestamp": net["timestamp"],
                        "rx_bps": net["rx_bps"] or 0.0,
                        "tx_bps": net["tx_bps"] or 0.0,
                        "rx_pps": net["rx_pps"] or 0.0,
                        "tx_pps": net["tx_pps"] or 0.0,
                        "rx_drop_ps": net["rx_drop_ps"] or 0.0,
                        "tx_drop_ps": net["tx_drop_ps"] or 0.0,
                        "softirq_total": soft_total,
                        "top_irq": top_irq["irq_name"] if top_irq else "N/A",
                        "top_irq_rate": float(top_irq["total_rate"]) if top_irq else 0.0,
                    }
                )
        return out

    def upsert_system(self, system: SystemRecord) -> None:
        with self._lock:
            with self._conn() as conn:
                conn.execute(
                    """
                    INSERT INTO systems(
                        id, name, hostname, address, port, os_distribution, os_version, kernel,
                        architecture, agent_version, status, last_seen, created_at, updated_at,
                        cpu_count, cpu_model, memory_total_kb, numa_nodes, interfaces_json,
                        ip_addresses_json, mode
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name=excluded.name,
                        hostname=excluded.hostname,
                        address=excluded.address,
                        port=excluded.port,
                        os_distribution=excluded.os_distribution,
                        os_version=excluded.os_version,
                        kernel=excluded.kernel,
                        architecture=excluded.architecture,
                        agent_version=excluded.agent_version,
                        status=excluded.status,
                        last_seen=excluded.last_seen,
                        updated_at=excluded.updated_at,
                        cpu_count=excluded.cpu_count,
                        cpu_model=excluded.cpu_model,
                        memory_total_kb=excluded.memory_total_kb,
                        numa_nodes=excluded.numa_nodes,
                        interfaces_json=excluded.interfaces_json,
                        ip_addresses_json=excluded.ip_addresses_json,
                        mode=excluded.mode
                    """,
                    (
                        system.id,
                        system.name,
                        system.hostname,
                        system.address,
                        system.port,
                        system.os_distribution,
                        system.os_version,
                        system.kernel,
                        system.architecture,
                        system.agent_version,
                        system.status,
                        system.last_seen,
                        system.created_at,
                        system.updated_at,
                        system.cpu_count,
                        system.cpu_model,
                        system.memory_total_kb,
                        system.numa_nodes,
                        json.dumps(system.interfaces, separators=(",", ":")),
                        json.dumps(system.ip_addresses, separators=(",", ":")),
                        system.mode,
                    ),
                )

    def list_systems(self) -> List[SystemRecord]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM systems ORDER BY name").fetchall()
        out: List[SystemRecord] = []
        for row in rows:
            out.append(
                SystemRecord(
                    id=row["id"],
                    name=row["name"],
                    hostname=row["hostname"],
                    address=row["address"],
                    port=row["port"],
                    os_distribution=row["os_distribution"],
                    os_version=row["os_version"],
                    kernel=row["kernel"],
                    architecture=row["architecture"],
                    agent_version=row["agent_version"],
                    status=row["status"],
                    last_seen=row["last_seen"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                    cpu_count=row["cpu_count"],
                    cpu_model=row["cpu_model"],
                    memory_total_kb=row["memory_total_kb"],
                    numa_nodes=row["numa_nodes"],
                    interfaces=json.loads(row["interfaces_json"] or "[]"),
                    ip_addresses=json.loads(row["ip_addresses_json"] or "[]"),
                    mode=row["mode"] or "remote",
                )
            )
        return out

    def get_system(self, sut_id: str) -> Optional[SystemRecord]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM systems WHERE id = ?", (sut_id,)).fetchone()
        if not row:
            return None
        return SystemRecord(
            id=row["id"],
            name=row["name"],
            hostname=row["hostname"],
            address=row["address"],
            port=row["port"],
            os_distribution=row["os_distribution"],
            os_version=row["os_version"],
            kernel=row["kernel"],
            architecture=row["architecture"],
            agent_version=row["agent_version"],
            status=row["status"],
            last_seen=row["last_seen"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            cpu_count=row["cpu_count"],
            cpu_model=row["cpu_model"],
            memory_total_kb=row["memory_total_kb"],
            numa_nodes=row["numa_nodes"],
            interfaces=json.loads(row["interfaces_json"] or "[]"),
            ip_addresses=json.loads(row["ip_addresses_json"] or "[]"),
            mode=row["mode"] or "remote",
        )

    def delete_system(self, sut_id: str) -> None:
        with self._lock:
            with self._conn() as conn:
                conn.execute("DELETE FROM systems WHERE id = ?", (sut_id,))

    def add_heartbeat(self, sut_id: str, timestamp: float, uptime_seconds: float, agent_version: str) -> None:
        with self._lock:
            with self._conn() as conn:
                conn.execute(
                    """
                    INSERT INTO agent_heartbeats(sut_id, timestamp, uptime_seconds, agent_version)
                    VALUES(?, ?, ?, ?)
                    """,
                    (sut_id, timestamp, uptime_seconds, agent_version),
                )
                conn.execute(
                    """
                    UPDATE systems
                    SET last_seen = ?, updated_at = ?, agent_version = ?, status = 'ONLINE'
                    WHERE id = ?
                    """,
                    (timestamp, timestamp, agent_version, sut_id),
                )


settings.ensure_dirs()
STORE = SqliteStore(str(settings.db_path), settings.metric_retention)
