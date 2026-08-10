from __future__ import annotations

import json
import sqlite3
from threading import Lock
from typing import Dict, List

from .models import HostSample, IrqSample
from .config import settings

class SqliteStore:
    def __init__(self, db_path: str, retention: int) -> None:
        self._lock = Lock()
        self._db_path = db_path
        self._retention = retention
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self._db_path)
        c.row_factory = sqlite3.Row
        return c

    def _init_db(self) -> None:
        with self._conn() as c:
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS irq_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    sut_ip TEXT NOT NULL,
                    irq TEXT NOT NULL,
                    irq_name TEXT NOT NULL,
                    nic TEXT NOT NULL DEFAULT '',
                    queue TEXT NOT NULL DEFAULT '',
                    direction TEXT NOT NULL DEFAULT 'Other',
                    source_class TEXT NOT NULL DEFAULT 'other',
                    total_rate REAL NOT NULL,
                    cpu_rates_json TEXT NOT NULL,
                    affinity_list TEXT NOT NULL
                )
                """
            )
            self._ensure_column(c, "irq_samples", "nic", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(c, "irq_samples", "queue", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(c, "irq_samples", "direction", "TEXT NOT NULL DEFAULT 'Other'")
            self._ensure_column(c, "irq_samples", "source_class", "TEXT NOT NULL DEFAULT 'other'")
            c.execute("CREATE INDEX IF NOT EXISTS idx_irq_host_id ON irq_samples(sut_ip, id)")
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS host_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    sut_ip TEXT NOT NULL,
                    nic TEXT NOT NULL,
                    rx_bps REAL NOT NULL,
                    tx_bps REAL NOT NULL,
                    rx_pps REAL NOT NULL,
                    tx_pps REAL NOT NULL,
                    rx_drop_ps REAL NOT NULL,
                    tx_drop_ps REAL NOT NULL,
                    softirq_rates_json TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            self._ensure_column(c, "host_samples", "details_json", "TEXT NOT NULL DEFAULT '{}'")
            c.execute("CREATE INDEX IF NOT EXISTS idx_host_host_id ON host_samples(sut_ip, id)")

    def _ensure_column(self, conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        existing = {row[1] for row in rows}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def add_samples(self, samples: List[IrqSample]) -> None:
        if not samples:
            return
        with self._lock:
            by_host = set()
            with self._conn() as c:
                for s in samples:
                    by_host.add(s.sut_ip)
                    c.execute(
                        """
                        INSERT INTO irq_samples(
                            timestamp, sut_ip, irq, irq_name, nic, queue, direction, source_class, total_rate, cpu_rates_json, affinity_list
                        )
                        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            s.timestamp,
                            s.sut_ip,
                            s.irq,
                            s.irq_name,
                            s.nic,
                            s.queue,
                            s.direction,
                            s.source_class,
                            s.total_rate,
                            json.dumps(s.cpu_rates, separators=(",", ":")),
                            s.affinity_list,
                        ),
                    )
                for host in by_host:
                    c.execute(
                        """
                        DELETE FROM irq_samples
                        WHERE sut_ip = ? AND id NOT IN (
                            SELECT id FROM irq_samples WHERE sut_ip = ? ORDER BY id DESC LIMIT ?
                        )
                        """,
                        (host, host, self._retention),
                    )

    def add_host_samples(self, samples: List[HostSample]) -> None:
        if not samples:
            return
        with self._lock:
            by_host = set()
            with self._conn() as c:
                for s in samples:
                    by_host.add(s.sut_ip)
                    c.execute(
                        """
                        INSERT INTO host_samples(
                            timestamp, sut_ip, nic, rx_bps, tx_bps, rx_pps, tx_pps, rx_drop_ps, tx_drop_ps, softirq_rates_json, details_json
                        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            s.timestamp,
                            s.sut_ip,
                            s.nic,
                            s.rx_bps,
                            s.tx_bps,
                            s.rx_pps,
                            s.tx_pps,
                            s.rx_drop_ps,
                            s.tx_drop_ps,
                            json.dumps(s.softirq_rates, separators=(",", ":")),
                            json.dumps(s.details, separators=(",", ":")),
                        ),
                    )
                for host in by_host:
                    c.execute(
                        """
                        DELETE FROM host_samples
                        WHERE sut_ip = ? AND id NOT IN (
                            SELECT id FROM host_samples WHERE sut_ip = ? ORDER BY id DESC LIMIT ?
                        )
                        """,
                        (host, host, self._retention),
                    )

    def hosts(self) -> List[str]:
        with self._conn() as c:
            rows = c.execute(
                """
                SELECT sut_ip FROM irq_samples
                UNION
                SELECT sut_ip FROM host_samples
                ORDER BY sut_ip
                """
            ).fetchall()
            return [r["sut_ip"] for r in rows]

    def latest(self, sut_ip: str, limit: int = 300) -> List[IrqSample]:
        with self._conn() as c:
            rows = c.execute(
                """
                SELECT timestamp, sut_ip, irq, irq_name, nic, queue, direction, source_class, total_rate, cpu_rates_json, affinity_list
                FROM irq_samples
                WHERE sut_ip = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (sut_ip, limit),
            ).fetchall()
        out = []
        for r in reversed(rows):
            out.append(
                IrqSample(
                    timestamp=r["timestamp"],
                    sut_ip=r["sut_ip"],
                    irq=r["irq"],
                    irq_name=r["irq_name"],
                    nic=r["nic"] or "",
                    queue=r["queue"] or "",
                    direction=r["direction"] or "Other",
                    source_class=r["source_class"] or "other",
                    total_rate=r["total_rate"],
                    cpu_rates=json.loads(r["cpu_rates_json"] or "{}"),
                    affinity_list=r["affinity_list"],
                )
            )
        return out

    def latest_host(self, sut_ip: str, limit: int = 120) -> List[HostSample]:
        with self._conn() as c:
            rows = c.execute(
                """
                SELECT timestamp, sut_ip, nic, rx_bps, tx_bps, rx_pps, tx_pps, rx_drop_ps, tx_drop_ps, softirq_rates_json
                      , details_json
                FROM host_samples
                WHERE sut_ip = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (sut_ip, limit),
            ).fetchall()
        out = []
        for r in reversed(rows):
            out.append(
                HostSample(
                    timestamp=r["timestamp"],
                    sut_ip=r["sut_ip"],
                    nic=r["nic"],
                    rx_bps=r["rx_bps"],
                    tx_bps=r["tx_bps"],
                    rx_pps=r["rx_pps"],
                    tx_pps=r["tx_pps"],
                    rx_drop_ps=r["rx_drop_ps"],
                    tx_drop_ps=r["tx_drop_ps"],
                    softirq_rates=json.loads(r["softirq_rates_json"] or "{}"),
                    details=json.loads(r["details_json"] or "{}"),
                )
            )
        return out

    def summary_current(self) -> List[Dict[str, float]]:
        with self._conn() as c:
            hosts = [r["sut_ip"] for r in c.execute("SELECT sut_ip FROM host_samples GROUP BY sut_ip").fetchall()]
            out: List[Dict[str, float]] = []
            for h in hosts:
                row = c.execute(
                    """
                    SELECT timestamp, nic, rx_bps, tx_bps, rx_pps, tx_pps, rx_drop_ps, tx_drop_ps, softirq_rates_json, details_json
                    FROM host_samples
                    WHERE sut_ip = ?
                    ORDER BY id DESC LIMIT 1
                    """,
                    (h,),
                ).fetchone()
                if not row:
                    continue
                soft = json.loads(row["softirq_rates_json"] or "{}")
                soft_total = float(sum(float(v) for v in soft.values()))
                top_irq = c.execute(
                    """
                    SELECT irq_name, nic, queue, direction, total_rate FROM irq_samples
                    WHERE sut_ip = ?
                    ORDER BY id DESC LIMIT 1
                    """,
                    (h,),
                ).fetchone()
                out.append(
                    {
                        "sut_ip": h,
                        "timestamp": row["timestamp"],
                        "nic": row["nic"],
                        "rx_bps": row["rx_bps"],
                        "tx_bps": row["tx_bps"],
                        "rx_pps": row["rx_pps"],
                        "tx_pps": row["tx_pps"],
                        "rx_drop_ps": row["rx_drop_ps"],
                        "tx_drop_ps": row["tx_drop_ps"],
                        "softirq_total": soft_total,
                        "top_irq": top_irq["irq_name"] if top_irq else "",
                        "top_irq_nic": top_irq["nic"] if top_irq else "",
                        "top_irq_queue": top_irq["queue"] if top_irq else "",
                        "top_irq_direction": top_irq["direction"] if top_irq else "",
                        "top_irq_rate": float(top_irq["total_rate"]) if top_irq else 0.0,
                        "details": json.loads(row["details_json"] or "{}"),
                    }
                )
            return sorted(out, key=lambda x: x["sut_ip"])


settings.ensure_dirs()
STORE = SqliteStore(str(settings.db_path), settings.metric_retention)
