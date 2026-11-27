from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional
from uuid import uuid4

from .collectors.storage import METRICS as STORAGE_METRICS
from .config import settings
from .models import CPUTopologyEntry, CollectionSession, ExportFile, InterfaceInfo, IRQSample, NetworkSample, SoftIRQSample, SystemInfo, SystemRecord

# Every time-series table and the column that names its SUT.
SERIES_TABLES = {
    "irq_samples": "sut_ip",
    "softirq_samples": "sut_ip",
    "network_samples": "sut_ip",
    "interface_samples": "sut_ip",
    "system_samples": "sut_ip",
    "cpu_topology_samples": "sut_id",
    "cpu_utilization_samples": "sut_id",
    "softnet_samples": "sut_id",
    "storage_samples": "sut_id",
    "pcie_samples": "sut_id",
    "agent_heartbeats": "sut_id",
}
_JSON_TABLES = ("softnet_samples", "storage_samples", "pcie_samples")
_RUNNING_SESSION_MAX_AGE = 7200.0


class _Session:
    def __init__(self, conn: sqlite3.Connection, owned: bool) -> None:
        self._conn = conn
        self._owned = owned

    def __enter__(self) -> sqlite3.Connection:
        return self._conn

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        if self._owned:
            try:
                if exc_type is None:
                    self._conn.commit()
                else:
                    self._conn.rollback()
            finally:
                self._conn.close()
        return False


class SqliteStore:
    def __init__(self, db_path: str, retention_minutes: float = 30.0) -> None:
        self._db_path = db_path
        self._retention_seconds = float(retention_minutes) * 60.0
        self._lock = threading.RLock()
        self._tx = threading.local()
        self._init_db()

    def _conn(self) -> _Session:
        active = getattr(self._tx, "conn", None)
        if active is not None:
            return _Session(active, owned=False)
        conn = sqlite3.connect(self._db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=NORMAL")
        return _Session(conn, owned=True)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        # Every store call made inside the block shares one connection and one commit.
        if getattr(self._tx, "conn", None) is not None:
            yield
            return
        with self._lock, self._conn() as conn:
            self._tx.conn = conn
            try:
                yield
            finally:
                self._tx.conn = None

    def journal_mode(self) -> str:
        with self._conn() as conn:
            return str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()

    def prune(self, sut_id: str, now_ts: Optional[float] = None) -> None:
        cutoff = float(time.time() if now_ts is None else now_ts) - self._retention_seconds
        with self._lock, self._conn() as conn:
            running = conn.execute(
                """
                SELECT MIN(start_time) AS start FROM sessions
                WHERE status = 'running' AND start_time > ? AND (sut_id = ? OR (sut_id = '' AND ? = 'local'))
                """,
                (time.time() - _RUNNING_SESSION_MAX_AGE, sut_id, sut_id),
            ).fetchone()
            if running and running["start"] is not None:
                cutoff = min(cutoff, float(running["start"]))
            for table, column in SERIES_TABLES.items():
                # The newest tick survives so a SUT that went quiet keeps its last snapshot.
                conn.execute(
                    f"DELETE FROM {table} WHERE {column} = ? AND timestamp < ? "
                    f"AND timestamp < (SELECT MAX(timestamp) FROM {table} WHERE {column} = ?)",
                    (sut_id, cutoff, sut_id),
                )

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
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
                CREATE TABLE IF NOT EXISTS cpu_topology_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    sut_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cpu_topology_host_ts ON cpu_topology_samples(sut_id, timestamp)")

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cpu_utilization_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    sut_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cpu_util_host_ts ON cpu_utilization_samples(sut_id, timestamp)")

            for table in _JSON_TABLES:
                conn.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {table} (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp REAL NOT NULL,
                        sut_id TEXT NOT NULL,
                        payload_json TEXT NOT NULL
                    )
                    """
                )
                conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_sut_ts ON {table}(sut_id, timestamp)")

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
            self._ensure_column(conn, "sessions", "report_path", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "sessions", "report_status", "TEXT NOT NULL DEFAULT 'none'")
            self._ensure_column(conn, "sessions", "report_error", "TEXT NOT NULL DEFAULT ''")

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
                DELETE FROM session_files
                WHERE id NOT IN (
                    SELECT MIN(id)
                    FROM session_files
                    GROUP BY session_id, path
                )
                """
            )
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_session_files_unique ON session_files(session_id, path)")

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
            self._ensure_column(conn, "systems", "sampling_interval_seconds", "REAL")

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

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS diagnostic_jobs (
                    job_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    sut_id TEXT NOT NULL,
                    categories_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    created_at REAL NOT NULL,
                    claimed_at REAL NOT NULL DEFAULT 0,
                    lease_token TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_diagnostic_jobs_sut ON diagnostic_jobs(sut_id, status, created_at)")

    def enqueue_diagnostics(self, session_id: str, sut_id: str, categories: List[str]) -> str:
        job_id = uuid4().hex
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO diagnostic_jobs(job_id, session_id, sut_id, categories_json, created_at) VALUES(?, ?, ?, ?, ?)",
                (job_id, session_id, sut_id, json.dumps(categories), time.time()),
            )
        return job_id

    def claim_diagnostics(self, sut_id: str) -> Optional[Dict[str, Any]]:
        now = time.time()
        with self._lock, self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT * FROM diagnostic_jobs WHERE sut_id = ?
                AND (status = 'queued' OR (status = 'running' AND claimed_at < ?))
                ORDER BY created_at LIMIT 1
                """,
                (sut_id, now - 300.0),
            ).fetchone()
            if row is None:
                return None
            job = dict(row)
            job["lease_token"] = uuid4().hex
            conn.execute(
                "UPDATE diagnostic_jobs SET status = 'running', claimed_at = ?, lease_token = ? WHERE job_id = ?",
                (now, job["lease_token"], job["job_id"]),
            )
            return {"job_id": job["job_id"], "session_id": job["session_id"], "lease_token": job["lease_token"], "categories": json.loads(job["categories_json"])}

    def diagnostic_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM diagnostic_jobs WHERE job_id = ?", (job_id,)).fetchone()
        return dict(row) if row else None

    def diagnostic_jobs(self, session_id: str) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT job_id, status, error, created_at FROM diagnostic_jobs WHERE session_id = ? ORDER BY created_at",
                (session_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def finish_diagnostics(self, job_id: str, lease_token: str, error: str = "") -> bool:
        with self._lock, self._conn() as conn:
            changed = conn.execute(
                "UPDATE diagnostic_jobs SET status = ?, error = ? WHERE job_id = ? AND lease_token = ? AND status = 'running'",
                ("failed" if error else "completed", error, job_id, lease_token),
            ).rowcount
        return changed == 1

    def _ensure_column(self, conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        existing = {row[1] for row in rows}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def add_irq_samples(self, samples: List[IRQSample]) -> None:
        if not samples:
            return
        with self._lock:
            with self._conn() as conn:
                for sample in samples:
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

    def add_network_samples(self, samples: List[NetworkSample]) -> None:
        if not samples:
            return
        with self._lock:
            with self._conn() as conn:
                for sample in samples:
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

    def add_cpu_topology(self, sut_id: str, topology: List[CPUTopologyEntry], timestamp: Optional[float] = None) -> None:
        if not topology:
            return
        ts = float(timestamp or time.time())
        payload = [item.model_dump() for item in topology]
        self._add_json("cpu_topology_samples", sut_id, ts, payload, dedupe=True)

    def add_cpu_utilization(self, sut_id: str, cpu_utilization: Dict[str, float], timestamp: Optional[float] = None) -> None:
        if not cpu_utilization:
            return
        ts = float(timestamp or time.time())
        payload = {str(k): float(v) for k, v in cpu_utilization.items()}
        with self._lock:
            with self._conn() as conn:
                conn.execute(
                    """
                    INSERT INTO cpu_utilization_samples(timestamp, sut_id, payload_json)
                    VALUES(?, ?, ?)
                    """,
                    (ts, sut_id, json.dumps(payload, separators=(",", ":"))),
                )

    def latest_cpu_utilization(self, sut_id: str) -> Dict[str, float]:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT payload_json
                FROM cpu_utilization_samples
                WHERE sut_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (sut_id,),
            ).fetchone()
        if not row:
            return {}
        data = json.loads(row["payload_json"] or "{}")
        out: Dict[str, float] = {}
        for key, value in data.items():
            try:
                out[str(key)] = float(value)
            except Exception:
                continue
        return out

    def cpu_utilization_series(
        self, sut_id: str, since_ts: float, to_ts: Optional[float] = None, per_cpu: bool = True,
    ) -> List[Dict[str, Any]]:
        end_ts = float(time.time() if to_ts is None else to_ts)
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT timestamp, payload_json
                FROM cpu_utilization_samples
                WHERE sut_id = ? AND timestamp >= ? AND timestamp <= ?
                ORDER BY timestamp ASC
                """,
                (sut_id, float(since_ts), end_ts),
            ).fetchall()
        out: List[Dict[str, Any]] = []
        for row in rows:
            payload = json.loads(row["payload_json"] or "{}")
            cpu_values: Dict[str, float] = {}
            vals: List[float] = []
            for key, value in payload.items():
                try:
                    numeric = float(value)
                except Exception:
                    continue
                cpu_values[str(key)] = numeric
                vals.append(numeric)
            point: Dict[str, Any] = {
                "timestamp": float(row["timestamp"]),
                "avg": float(sum(vals) / len(vals)) if vals else 0.0,
                "max": float(max(vals)) if vals else 0.0,
            }
            if per_cpu:
                point["cpus"] = cpu_values
            out.append(point)
        return out

    def system_series(self, sut_id: str, since_ts: float, to_ts: Optional[float] = None) -> List[Dict[str, Any]]:
        end_ts = float(time.time() if to_ts is None else to_ts)
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT timestamp, payload_json
                FROM system_samples
                WHERE sut_ip = ? AND timestamp >= ? AND timestamp <= ?
                ORDER BY timestamp ASC
                """,
                (sut_id, float(since_ts), end_ts),
            ).fetchall()
        out: List[Dict[str, Any]] = []
        for row in rows:
            payload = json.loads(row["payload_json"] or "{}")
            out.append(
                {
                    "timestamp": float(row["timestamp"]),
                    "hostname": str(payload.get("hostname", "unknown")),
                    "os_distribution": str(payload.get("os_distribution", "Unknown")),
                    "kernel": str(payload.get("kernel", "Unknown")),
                    "cpu_count": int(payload.get("cpu_count", 0) or 0),
                    "cpu_mhz": float(payload.get("cpu_mhz")) if payload.get("cpu_mhz") is not None else None,
                    "loadavg_1m": float(payload.get("loadavg_1m", 0.0) or 0.0),
                    "memory_available_kb": int(payload.get("memory_available_kb", 0) or 0),
                    "memory_total_kb": int(payload.get("memory_total_kb", 0) or 0),
                }
            )
        return out

    def network_interface_series(self, sut_id: str, since_ts: float, to_ts: Optional[float] = None) -> Dict[str, List[Dict[str, float]]]:
        end_ts = float(time.time() if to_ts is None else to_ts)
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT timestamp, interface,
                       rx_bps, tx_bps,
                       rx_pps, tx_pps,
                       rx_err_ps, tx_err_ps,
                       rx_drop_ps, tx_drop_ps
                FROM network_samples
                WHERE sut_ip = ? AND timestamp >= ? AND timestamp <= ?
                ORDER BY timestamp ASC
                """,
                (sut_id, float(since_ts), end_ts),
            ).fetchall()
        out: Dict[str, List[Dict[str, float]]] = {}
        for row in rows:
            iface = str(row["interface"])
            out.setdefault(iface, []).append(
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

    def irq_source_series(self, sut_id: str, since_ts: float, to_ts: Optional[float] = None, top_n: int = 8) -> Dict[str, Any]:
        end_ts = float(time.time() if to_ts is None else to_ts)
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT timestamp, irq, irq_name, total_rate, cpu_rates_json, nic
                FROM irq_samples
                WHERE sut_ip = ? AND timestamp >= ? AND timestamp <= ?
                ORDER BY timestamp ASC
                """,
                (sut_id, float(since_ts), end_ts),
            ).fetchall()

        source_totals: Dict[str, float] = {}
        by_ts: Dict[float, Dict[str, float]] = {}
        meta: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            key = str(row["irq_name"] or row["irq"])
            rate = float(row["total_rate"] or 0.0)
            ts = float(row["timestamp"])
            source_totals[key] = source_totals.get(key, 0.0) + rate
            by_ts.setdefault(ts, {})
            by_ts[ts][key] = by_ts[ts].get(key, 0.0) + rate
            cpu_rates = json.loads(row["cpu_rates_json"] or "{}")
            top_cpu = "N/A"
            top_cpu_rate = 0.0
            for cpu, cpu_rate in cpu_rates.items():
                try:
                    value = float(cpu_rate)
                except Exception:
                    continue
                if value > top_cpu_rate:
                    top_cpu_rate = value
                    top_cpu = str(cpu)
            current = meta.get(key)
            if current is None or rate > float(current.get("peak_rate", 0.0)):
                meta[key] = {
                    "irq": str(row["irq"]),
                    "name": key,
                    "nic": str(row["nic"] or ""),
                    "peak_rate": rate,
                    "top_cpu": top_cpu,
                }

        top_sources = [name for name, _ in sorted(source_totals.items(), key=lambda kv: kv[1], reverse=True)[: max(1, int(top_n))]]

        points: List[Dict[str, Any]] = []
        for ts in sorted(by_ts.keys()):
            source_rates = {source: float(by_ts[ts].get(source, 0.0)) for source in top_sources}
            points.append({"timestamp": ts, "source_rates": source_rates, "total_irq_rate": float(sum(by_ts[ts].values()))})

        ranking = []
        for source in top_sources:
            source_points = [point["source_rates"][source] for point in points]
            latest = float(source_points[-1]) if source_points else 0.0
            peak = float(max(source_points)) if source_points else 0.0
            ranking.append(
                {
                    "source": source,
                    "irq": str(meta.get(source, {}).get("irq", "N/A")),
                    "nic": str(meta.get(source, {}).get("nic", "")),
                    "top_cpu": str(meta.get(source, {}).get("top_cpu", "N/A")),
                    "avg_rate": float(source_totals.get(source, 0.0) / max(1, len(points))),
                    "peak_rate": peak,
                    "latest_rate": latest,
                }
            )

        return {
            "top_sources": ranking,
            "points": points,
        }

    def latest_cpu_topology(self, sut_id: str) -> List[CPUTopologyEntry]:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT payload_json
                FROM cpu_topology_samples
                WHERE sut_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (sut_id,),
            ).fetchone()
        if not row:
            return []
        data = json.loads(row["payload_json"] or "[]")
        out: List[CPUTopologyEntry] = []
        for item in data:
            out.append(CPUTopologyEntry.model_validate(item))
        out.sort(key=lambda x: x.cpu_id)
        return out

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

    def latest_irq_timestamp_at(self, sut_ip: str, to_ts: Optional[float] = None) -> Optional[float]:
        with self._conn() as conn:
            if to_ts is None:
                row = conn.execute(
                    """
                    SELECT MAX(timestamp) AS ts
                    FROM irq_samples
                    WHERE sut_ip = ?
                    """,
                    (sut_ip,),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT MAX(timestamp) AS ts
                    FROM irq_samples
                    WHERE sut_ip = ? AND timestamp <= ?
                    """,
                    (sut_ip, float(to_ts)),
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

    def irq_rate_series(self, sut_ip: str, since_ts: float, to_ts: Optional[float] = None) -> List[Dict[str, float]]:
        with self._conn() as conn:
            if to_ts is None:
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
            else:
                rows = conn.execute(
                    """
                    SELECT timestamp, SUM(total_rate) AS irq_rate
                    FROM irq_samples
                    WHERE sut_ip = ? AND timestamp >= ? AND timestamp <= ?
                    GROUP BY timestamp
                    ORDER BY timestamp ASC
                    """,
                    (sut_ip, since_ts, float(to_ts)),
                ).fetchall()
        return [{"timestamp": float(r["timestamp"]), "irq_rate": float(r["irq_rate"] or 0.0)} for r in rows]

    def network_rate_series(self, sut_ip: str, since_ts: float, to_ts: Optional[float] = None) -> List[Dict[str, float]]:
        with self._conn() as conn:
            if to_ts is None:
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
            else:
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
                    WHERE sut_ip = ? AND timestamp >= ? AND timestamp <= ?
                    GROUP BY timestamp
                    ORDER BY timestamp ASC
                    """,
                    (sut_ip, since_ts, float(to_ts)),
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

    def softirq_series(
        self, sut_ip: str, since_ts: float, to_ts: Optional[float] = None, per_cpu: bool = True,
    ) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            if to_ts is None:
                rows = conn.execute(
                    """
                    SELECT timestamp, rates_json, per_cpu_rates_json
                    FROM softirq_samples
                    WHERE sut_ip = ? AND timestamp >= ?
                    ORDER BY timestamp ASC
                    """,
                    (sut_ip, since_ts),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT timestamp, rates_json, per_cpu_rates_json
                    FROM softirq_samples
                    WHERE sut_ip = ? AND timestamp >= ? AND timestamp <= ?
                    ORDER BY timestamp ASC
                    """,
                    (sut_ip, since_ts, float(to_ts)),
                ).fetchall()
        out: List[Dict[str, Any]] = []
        for row in rows:
            rates = json.loads(row["rates_json"] or "{}")
            sample: Dict[str, Any] = {
                "timestamp": float(row["timestamp"]),
                "rates": {k: float(v) for k, v in rates.items()},
            }
            if per_cpu:
                # One value per CPU and sample: skipped for callers that only chart the class totals.
                cpu_rates = json.loads(row["per_cpu_rates_json"] or "{}")
                sample["per_cpu_rates"] = {str(k): float(v) for k, v in cpu_rates.items()}
            out.append(sample)
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
                        kernel, collector_version, output_dir, categories_json, error, report_path, report_status, report_error
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        session.report_path,
                        session.report_status,
                        session.report_error,
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

    def update_session_report(self, session_id: str, report_status: str, report_path: str = "", report_error: str = "") -> None:
        with self._lock:
            with self._conn() as conn:
                conn.execute(
                    """
                    UPDATE sessions
                    SET report_status = ?, report_path = ?, report_error = ?
                    WHERE session_id = ?
                    """,
                    (report_status, report_path, report_error, session_id),
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
                        ON CONFLICT(session_id, path) DO UPDATE SET
                            name=excluded.name,
                            category=excluded.category,
                            format=excluded.format,
                            size_bytes=excluded.size_bytes,
                            created_at=excluded.created_at
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
                    report_path=row["report_path"] or "",
                    report_status=row["report_status"] or "none",
                    report_error=row["report_error"] or "",
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
            report_path=row["report_path"] or "",
            report_status=row["report_status"] or "none",
            report_error=row["report_error"] or "",
        )

    def session_files(self, session_id: str) -> List[ExportFile]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT name, category, format, path, size_bytes FROM session_files WHERE session_id = ? ORDER BY category, name",
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

    def delete_session(self, session_id: str) -> bool:
        with self._lock:
            with self._conn() as conn:
                deleted = conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,)).rowcount
                conn.execute("DELETE FROM session_files WHERE session_id = ?", (session_id,))
                conn.execute("DELETE FROM diagnostic_jobs WHERE session_id = ?", (session_id,))
        return deleted > 0

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
                        ip_addresses_json, mode, sampling_interval_seconds
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        mode=excluded.mode,
                        sampling_interval_seconds=excluded.sampling_interval_seconds
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
                        system.sampling_interval_seconds,
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
                    sampling_interval_seconds=row["sampling_interval_seconds"],
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
            sampling_interval_seconds=row["sampling_interval_seconds"],
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

    def _add_json(self, table: str, sut_id: str, timestamp: float, payload: Any, dedupe: bool = False) -> None:
        text = json.dumps(payload, separators=(",", ":"))
        with self._lock, self._conn() as conn:
            if dedupe:
                latest = conn.execute(
                    f"SELECT id, payload_json FROM {table} WHERE sut_id = ? ORDER BY id DESC LIMIT 1", (sut_id,)
                ).fetchone()
                if latest and latest["payload_json"] == text:
                    conn.execute(f"UPDATE {table} SET timestamp = ? WHERE id = ?", (timestamp, latest["id"]))
                    return
            conn.execute(f"INSERT INTO {table}(timestamp, sut_id, payload_json) VALUES(?, ?, ?)", (timestamp, sut_id, text))

    def add_softnet(self, sut_id: str, payload: Dict[str, Any], timestamp: float) -> None:
        self._add_json("softnet_samples", sut_id, float(timestamp), payload)

    def add_storage(self, sut_id: str, devices: List[Dict[str, Any]], timestamp: float) -> None:
        self._add_json("storage_samples", sut_id, float(timestamp), devices)

    def add_pcie(self, sut_id: str, devices: List[Dict[str, Any]], timestamp: float) -> None:
        self._add_json("pcie_samples", sut_id, float(timestamp), devices, dedupe=True)

    def latest_json(self, table: str, sut_id: str, default: Any) -> Any:
        if table not in _JSON_TABLES:
            raise ValueError(f"unsupported table: {table}")
        with self._conn() as conn:
            row = conn.execute(
                f"SELECT payload_json FROM {table} WHERE sut_id = ? ORDER BY id DESC LIMIT 1", (sut_id,)
            ).fetchone()
        return json.loads(row["payload_json"]) if row else default

    def json_series(self, table: str, sut_id: str, since_ts: float, to_ts: Optional[float] = None) -> List[Dict[str, Any]]:
        if table not in _JSON_TABLES:
            raise ValueError(f"unsupported table: {table}")
        end_ts = float(time.time() if to_ts is None else to_ts)
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT timestamp, payload_json FROM {table} WHERE sut_id = ? AND timestamp >= ? AND timestamp <= ? ORDER BY timestamp ASC",
                (sut_id, float(since_ts), end_ts),
            ).fetchall()
        return [{"timestamp": float(row["timestamp"]), "data": json.loads(row["payload_json"])} for row in rows]

    def storage_series(self, sut_id: str, since_ts: float, to_ts: Optional[float] = None) -> Dict[str, List[Dict[str, float]]]:
        out: Dict[str, List[Dict[str, float]]] = {}
        for sample in self.json_series("storage_samples", sut_id, since_ts, to_ts):
            for device in sample["data"]:
                point = {"timestamp": sample["timestamp"]}
                for key in STORAGE_METRICS:
                    try:
                        point[key] = float(device.get(key) or 0.0)
                    except (TypeError, ValueError):
                        point[key] = 0.0
                out.setdefault(str(device.get("device", "unknown")), []).append(point)
        return out

    def softnet_series(self, sut_id: str, since_ts: float, to_ts: Optional[float] = None) -> List[Dict[str, float]]:
        out: List[Dict[str, float]] = []
        for sample in self.json_series("softnet_samples", sut_id, since_ts, to_ts):
            total = sample["data"].get("total", {}) if isinstance(sample["data"], dict) else {}
            point = {"timestamp": sample["timestamp"]}
            for key in ("processed", "dropped", "time_squeeze", "cpu_collision"):
                point[key] = float(total.get(key) or 0)
            out.append(point)
        return out

    def current_irq(self, sut_ip: str, limit: int = 2000) -> List[IRQSample]:
        timestamp = self.latest_irq_timestamp(sut_ip)
        return self.irq_at_timestamp(sut_ip, timestamp, limit=limit) if timestamp is not None else []

    def irq_capture_summary(self, sut_id: str, since_ts: float, to_ts: Optional[float] = None) -> List[Dict[str, Any]]:
        end_ts = float(time.time() if to_ts is None else to_ts)
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT timestamp, irq, irq_name, nic, source_class, direction, numa_node, affinity_list, total_rate, cpu_rates_json
                FROM irq_samples
                WHERE sut_ip = ? AND timestamp >= ? AND timestamp <= ?
                ORDER BY timestamp ASC
                """,
                (sut_id, float(since_ts), end_ts),
            ).fetchall()
        ticks = set()
        summary: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            ticks.add(row["timestamp"])
            item = summary.setdefault(str(row["irq"]), {"total": 0.0, "peak_rate": 0.0, "cpu": {}})
            rate = float(row["total_rate"] or 0.0)
            item["total"] += rate
            item["peak_rate"] = max(item["peak_rate"], rate)
            for key in ("irq", "irq_name", "nic", "source_class", "direction", "numa_node", "affinity_list"):
                item[key] = row[key]
            for cpu, value in json.loads(row["cpu_rates_json"] or "{}").items():
                item["cpu"][str(cpu)] = item["cpu"].get(str(cpu), 0.0) + float(value)
        count = max(1, len(ticks))
        out = []
        for item in summary.values():
            cpu_totals = item.pop("cpu")
            total = item.pop("total")
            out.append({**item, "total_rate": total / count, "cpu_rates": {cpu: value / count for cpu, value in cpu_totals.items()}})
        out.sort(key=lambda entry: entry["total_rate"], reverse=True)
        return out


settings.ensure_dirs()
STORE = SqliteStore(str(settings.db_path), settings.retention_minutes)
