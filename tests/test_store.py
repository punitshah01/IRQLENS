from __future__ import annotations

import sqlite3

import pytest

from backend.app.models import CollectionSession, CPUTopologyEntry, InterfaceInfo, IRQSample, NetworkSample, SoftIRQSample, SystemInfo
from backend.app.store import SERIES_TABLES, SqliteStore


def _system(timestamp: float) -> SystemInfo:
    return SystemInfo(
        timestamp=timestamp, hostname="sut", kernel="6.8", os_distribution="Linux", os_version="1", uptime_seconds=1.0,
        boot_time_epoch=0.0, loadavg_1m=0.0, loadavg_5m=0.0, loadavg_15m=0.0, cpu_count=2, cpu_model="Test",
        memory_total_kb=1, memory_available_kb=1, numa_nodes=1, running_as_root=False,
    )


def _tick(store: SqliteStore, sut: str, timestamp: float, marker: float) -> None:
    with store.transaction():
        store.add_irq_samples([IRQSample(timestamp=timestamp, sut_ip=sut, irq="16", irq_name="eth0", total_rate=marker, cpu_rates={"0": marker})])
        store.add_softirq_sample(SoftIRQSample(timestamp=timestamp, sut_ip=sut, rates={"NET_RX": marker}))
        store.add_network_samples([NetworkSample(timestamp=timestamp, sut_ip=sut, interface="eth0", rx_bps=marker)])
        store.add_interfaces(sut, [InterfaceInfo(timestamp=timestamp, name="eth0", state="up")])
        store.add_system(sut, _system(timestamp))
        store.add_cpu_topology(sut, [CPUTopologyEntry(cpu_id=0, numa_node=0, freq_khz=int(marker))], timestamp=timestamp)
        store.add_cpu_utilization(sut, {"0": marker}, timestamp=timestamp)
        store.add_softnet(sut, {"total": {"processed": marker}}, timestamp)
        store.add_storage(sut, [{"device": "nvme0n1", "read_iops": marker}], timestamp)
        store.add_pcie(sut, [{"bdf": "0000:5e:00.0", "aer_total": marker}], timestamp)
        store.add_heartbeat(sut, timestamp, 1.0, "1.2.0")
        store.prune(sut, timestamp)


def _timestamps(store: SqliteStore, sut: str) -> dict:
    out = {}
    with store._conn() as conn:
        for table, column in SERIES_TABLES.items():
            rows = conn.execute(f"SELECT timestamp FROM {table} WHERE {column} = ? ORDER BY timestamp", (sut,)).fetchall()
            out[table] = [row["timestamp"] for row in rows]
    return out


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("backend.app.store.time.time", lambda: now[0])
    return now


def test_wal_mode_and_connection_pragmas(tmp_path):
    path = tmp_path / "wal.db"
    store = SqliteStore(str(path), 30)
    assert store.journal_mode() == "wal"
    with store._conn() as conn:
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    other = sqlite3.connect(str(path))
    try:
        assert other.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        other.close()


def test_series_table_list_covers_every_timestamped_table(tmp_path):
    store = SqliteStore(str(tmp_path / "schema.db"), 30)
    with store._conn() as conn:
        tables = [row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        timestamped = {
            table for table in tables
            if any(column["name"] == "timestamp" for column in conn.execute(f"PRAGMA table_info({table})"))
        }
    assert timestamped == set(SERIES_TABLES)
    assert {"cpu_utilization_samples", "cpu_topology_samples", "storage_samples", "pcie_samples", "agent_heartbeats"} <= timestamped


def test_tick_is_one_transaction_and_rolls_back_together(tmp_path, clock):
    store = SqliteStore(str(tmp_path / "tx.db"), 30)
    with pytest.raises(RuntimeError):
        with store.transaction():
            store.add_irq_samples([IRQSample(timestamp=1000.0, sut_ip="sut", irq="16", irq_name="eth0", total_rate=1.0)])
            store.add_storage("sut", [{"device": "sda"}], 1000.0)
            assert store.current_irq("sut")
            raise RuntimeError("tick failed")
    assert all(not rows for rows in _timestamps(store, "sut").values())
    _tick(store, "sut", 1000.0, 1.0)
    assert all(rows == [1000.0] for rows in _timestamps(store, "sut").values())
    with store.transaction():
        with store.transaction():
            store.add_softnet("sut", {"total": {}}, 1001.0)
    assert store.latest_json("softnet_samples", "sut", None) == {"total": {}}


def test_time_retention_prunes_every_series_table(tmp_path, clock):
    store = SqliteStore(str(tmp_path / "retention.db"), 1)
    _tick(store, "sut", 1000.0, 1.0)
    _tick(store, "other", 1000.0, 1.0)
    clock[0] = 1030.0
    _tick(store, "sut", 1030.0, 2.0)
    assert all(rows == [1000.0, 1030.0] for rows in _timestamps(store, "sut").values())
    clock[0] = 1070.0
    _tick(store, "sut", 1070.0, 3.0)
    kept = _timestamps(store, "sut")
    assert set(kept) == set(SERIES_TABLES)
    assert all(rows == [1030.0, 1070.0] for rows in kept.values()), kept
    assert all(rows == [1000.0] for rows in _timestamps(store, "other").values())
    clock[0] = 5000.0
    store.prune("sut", 5000.0)
    assert all(rows == [1070.0] for rows in _timestamps(store, "sut").values())


def test_retention_is_time_based_not_row_based(tmp_path, clock):
    store = SqliteStore(str(tmp_path / "rows.db"), 30)
    with store.transaction():
        for second in range(60):
            timestamp = 1000.0 + second
            store.add_irq_samples([
                IRQSample(timestamp=timestamp, sut_ip="sut", irq=str(irq), irq_name=f"q{irq}", total_rate=1.0) for irq in range(100)
            ])
        store.prune("sut", 1059.0)
    assert len(_timestamps(store, "sut")["irq_samples"]) == 6000
    assert len(store.irq_rate_series("sut", 0.0, 2000.0)) == 60
    assert len(store.current_irq("sut", limit=5000)) == 100
    store.prune("sut", 99999.0)
    assert len(store.irq_rate_series("sut", 0.0, 2000.0)) == 1
    assert len(store.current_irq("sut", limit=5000)) == 100


def test_legacy_xml_file_rows_still_list(tmp_path):
    from backend.app.models import ExportFile

    store = SqliteStore(str(tmp_path / "legacy.db"), 30)
    store.add_session_files("old_capture", [
        ExportFile(name="system.xml", category="system", format="xml", path=str(tmp_path / "system.xml"), size_bytes=10),
    ])
    assert [(item.name, item.format) for item in store.session_files("old_capture")] == [("system.xml", "xml")]


def test_running_capture_window_is_never_pruned(tmp_path, clock):
    store = SqliteStore(str(tmp_path / "capture.db"), 1)
    store.create_session(CollectionSession(
        session_id="capture", sut_id="sut", status="running", start_time=995.0, hostname="sut",
        os_distribution="Linux", kernel="6.8", collector_version="test", output_dir=str(tmp_path),
    ))
    _tick(store, "sut", 1000.0, 1.0)
    clock[0] = 1600.0
    _tick(store, "sut", 1600.0, 2.0)
    assert all(rows == [1000.0, 1600.0] for rows in _timestamps(store, "sut").values())
    store.update_session_status("capture", "stopped", end_time=1600.0)
    store.prune("sut", 1600.0)
    assert all(rows == [1600.0] for rows in _timestamps(store, "sut").values())


def test_unchanged_snapshots_are_not_duplicated(tmp_path, clock):
    store = SqliteStore(str(tmp_path / "dedupe.db"), 30)
    topology = [CPUTopologyEntry(cpu_id=0, numa_node=0)]
    for timestamp in (1000.0, 1001.0, 1002.0):
        store.add_cpu_topology("sut", topology, timestamp=timestamp)
        store.add_pcie("sut", [{"bdf": "0000:5e:00.0", "aer_total": 0}], timestamp)
    store.add_pcie("sut", [{"bdf": "0000:5e:00.0", "aer_total": 1}], 1003.0)
    kept = _timestamps(store, "sut")
    assert kept["cpu_topology_samples"] == [1002.0]
    assert kept["pcie_samples"] == [1002.0, 1003.0]
    assert store.latest_cpu_topology("sut")[0].numa_node == 0
    assert store.latest_json("pcie_samples", "sut", [])[0]["aer_total"] == 1
    with pytest.raises(ValueError):
        store.latest_json("sessions", "sut", None)
