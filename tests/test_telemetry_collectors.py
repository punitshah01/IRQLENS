from __future__ import annotations

import os
from pathlib import Path

import pytest

from backend.app.collectors.irq import IRQCollector, build_irq_device_map
from backend.app.collectors.pcie import PCIeCollector, classify_pci_class, link_degraded, parse_aer_total, parse_link_speed
from backend.app.collectors.softnet import SoftnetCollector, parse_softnet_stat
from backend.app.collectors.storage import StorageCollector, parse_diskstats
from backend.app.collectors.system import collect_cpu_topology, irqbalance_status
from backend.app.services.visualization import cross_numa_mismatch, flatten_irq_row


def write(path: Path, text: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


SOFTNET_1 = """\
0000272d 00000000 00000001 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000
000034d9 00000002 00000003 00000000 00000000 00000000 00000000 00000000 00000004 00000000 00000000 00000000 00000002
"""
SOFTNET_2 = """\
0000272d 00000000 00000001 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000
000034e9 00000005 00000004 00000000 00000000 00000000 00000000 00000000 00000006 00000000 00000000 00000000 00000002
"""


def test_softnet_stat_parser_uses_cpu_column_and_hex():
    rows = parse_softnet_stat(SOFTNET_1)
    assert rows == {
        "0": {"processed": 0x272D, "dropped": 0, "time_squeeze": 1, "cpu_collision": 0},
        "2": {"processed": 0x34D9, "dropped": 2, "time_squeeze": 3, "cpu_collision": 4},
    }
    legacy = parse_softnet_stat("0000000a 00000001 00000002 00000000 00000000 00000000 00000000 00000000 00000003\nnot-hex 0 0\n\n")
    assert legacy == {"0": {"processed": 10, "dropped": 1, "time_squeeze": 2, "cpu_collision": 3}}
    assert parse_softnet_stat("") == {}


def test_softnet_collector_reports_per_cpu_and_aggregate_deltas(tmp_path):
    stat = write(tmp_path / "proc" / "net" / "softnet_stat", SOFTNET_1)
    collector = SoftnetCollector(proc_root=str(tmp_path / "proc"))
    first = collector.collect(1.0)
    assert first["available"] is True
    assert first["total"] == {"processed": 0, "dropped": 0, "time_squeeze": 0, "cpu_collision": 0}
    assert first["per_cpu"] == {}
    stat.write_text(SOFTNET_2, encoding="utf-8")
    second = collector.collect(2.0)
    assert second["interval_s"] == 2.0
    assert second["total"] == {"processed": 16, "dropped": 3, "time_squeeze": 1, "cpu_collision": 2}
    assert second["per_cpu"] == {"2": {"processed": 16, "dropped": 3, "time_squeeze": 1, "cpu_collision": 2}}
    assert SoftnetCollector(proc_root=str(tmp_path / "missing")).collect(1.0)["available"] is False


DISKSTATS_1 = """\
   7       0 loop0 100 0 800 10 0 0 0 0 0 10 10 0 0 0 0
 259       0 nvme0n1 1000 0 80000 500 2000 0 160000 4000 3 1500 4500 0 0 0 0
 259       1 nvme0n1p1 900 0 70000 400 1900 0 150000 3900 0 1400 4300 0 0 0 0
   8       0 sda 10 0 80 5 20 0 160 40 0 30 45 0 0 0 0
   8       1 sda1 10 0 80 5 20 0 160 40 0 30 45 0 0 0 0
 253       0 dm-0 5 0 40 1 5 0 40 1 0 2 2 0 0 0 0
   1       0 ram0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
 252       0 vda 1 0 8 1 1 0 8 1 0 1 1
"""
DISKSTATS_2 = DISKSTATS_1.replace(
    "nvme0n1 1000 0 80000 500 2000 0 160000 4000 3 1500 4500",
    "nvme0n1 1200 0 84096 600 2100 0 162048 4200 7 2500 5500",
)


def test_diskstats_parser_keeps_physical_whole_disks():
    assert sorted(parse_diskstats(DISKSTATS_1)) == ["nvme0n1", "sda", "vda"]
    assert sorted(parse_diskstats(DISKSTATS_1, include_virtual=True)) == ["dm-0", "loop0", "nvme0n1", "ram0", "sda", "vda"]
    assert parse_diskstats(DISKSTATS_1)["nvme0n1"] == {
        "reads": 1000, "sectors_read": 80000, "read_ms": 500, "writes": 2000,
        "sectors_written": 160000, "write_ms": 4000, "in_flight": 3, "io_ms": 1500,
    }
    assert parse_diskstats("garbage\n 8 0 sda 1 2\n") == {}


def test_storage_collector_rates_and_metadata(tmp_path):
    sys_root = tmp_path / "sys"
    diskstats = write(tmp_path / "proc" / "diskstats", DISKSTATS_1)
    write(sys_root / "block" / "nvme0n1" / "queue" / "scheduler", "[none] mq-deadline kyber\n")
    write(sys_root / "block" / "nvme0n1" / "queue" / "nr_requests", "1023\n")
    write(sys_root / "block" / "nvme0n1" / "queue" / "rotational", "0\n")
    write(sys_root / "block" / "nvme0n1" / "device" / "numa_node", "1\n")
    write(sys_root / "block" / "nvme0n1" / "device" / "model", "TEST SSD 4TB\n")
    for irq in ("130", "131", "132"):
        write(sys_root / "class" / "nvme" / "nvme0" / "device" / "msi_irqs" / irq)
    write(sys_root / "block" / "sda" / "queue" / "scheduler", "mq-deadline [bfq]\n")
    write(sys_root / "block" / "sda" / "queue" / "rotational", "1\n")
    write(sys_root / "block" / "sda" / "device" / "numa_node", "-1\n")

    collector = StorageCollector(proc_root=str(tmp_path / "proc"), sys_root=str(sys_root))
    first = {row["device"]: row for row in collector.collect(1.0)}
    assert sorted(first) == ["nvme0n1", "sda", "vda"]
    assert first["nvme0n1"]["read_iops"] == 0.0 and first["nvme0n1"]["queue_depth_in_flight"] == 3.0

    diskstats.write_text(DISKSTATS_2, encoding="utf-8")
    second = {row["device"]: row for row in collector.collect(2.0)}
    nvme = second["nvme0n1"]
    assert nvme["read_iops"] == pytest.approx(100.0)
    assert nvme["write_iops"] == pytest.approx(50.0)
    assert nvme["read_mb_s"] == pytest.approx(1.0)
    assert nvme["write_mb_s"] == pytest.approx(0.5)
    assert nvme["avg_read_await_ms"] == pytest.approx(0.5)
    assert nvme["avg_write_await_ms"] == pytest.approx(2.0)
    assert nvme["queue_depth_in_flight"] == 7.0
    assert nvme["util_pct"] == pytest.approx(50.0)
    assert (nvme["scheduler"], nvme["nr_requests"], nvme["rotational"], nvme["numa_node"]) == ("none", 1023, False, 1)
    assert nvme["model"] == "TEST SSD 4TB"
    assert nvme["msi_irqs"] == ["130", "131", "132"] and nvme["msi_irq_count"] == 3
    sda = second["sda"]
    assert sda["read_iops"] == 0.0 and sda["avg_read_await_ms"] == 0.0 and sda["util_pct"] == 0.0
    assert (sda["scheduler"], sda["rotational"], sda["numa_node"], sda["msi_irqs"]) == ("bfq", True, None, [])
    assert second["vda"]["scheduler"] == "N/A" and second["vda"]["nr_requests"] is None

    virtual = StorageCollector(proc_root=str(tmp_path / "proc"), sys_root=str(sys_root), include_virtual=True)
    assert {"loop0", "dm-0", "ram0"} <= {row["device"] for row in virtual.collect(1.0)}


def bdf(address: str) -> str:
    # Windows cannot create directory names containing ':'.
    return address.replace(":", "_") if os.name == "nt" else address


def _pci_device(root: Path, address: str, class_code: str, **files: str) -> Path:
    device = root / "bus" / "pci" / "devices" / bdf(address)
    write(device / "class", class_code + "\n")
    write(device / "vendor", "0x8086\n")
    write(device / "device", "0x1592\n")
    for name, text in files.items():
        write(device / name, text)
    return device


@pytest.fixture
def pci_sysfs(tmp_path):
    root = tmp_path / "sys"
    nic = _pci_device(
        root, "0000:5e:00.0", "0x020000", numa_node="1\n", driver="ice\n",
        current_link_speed="8.0 GT/s PCIe\n", max_link_speed="16.0 GT/s PCIe\n",
        current_link_width="8\n", max_link_width="16\n",
        aer_dev_correctable="RxErr 0\nBadTLP 2\nTOTAL_ERR_COR 2\n",
        aer_dev_nonfatal="TOTAL_ERR_NONFATAL 0\n", aer_dev_fatal="TOTAL_ERR_FATAL 0\n",
    )
    for irq in ("120", "121"):
        write(nic / "msi_irqs" / irq)
    (nic / "net" / "ens1f0").mkdir(parents=True)
    _pci_device(
        root, "0000:3b:00.0", "0x010802", numa_node="0\n", driver="nvme\n",
        current_link_speed="16.0 GT/s PCIe\n", max_link_speed="16.0 GT/s PCIe\n",
        current_link_width="4\n", max_link_width="4\n",
        aer_dev_correctable="TOTAL_ERR_COR 0\n", aer_dev_nonfatal="TOTAL_ERR_NONFATAL 0\n", aer_dev_fatal="TOTAL_ERR_FATAL 0\n",
    )
    _pci_device(
        root, "0000:af:00.0", "0x030000", numa_node="-1\n",
        current_link_speed="16.0 GT/s PCIe\n", max_link_speed="16.0 GT/s PCIe\n",
        current_link_width="8\n", max_link_width="16\n",
    )
    _pci_device(root, "0000:6a:00.0", "0x120000", current_link_speed="Unknown\n", max_link_speed="32.0 GT/s PCIe\n")
    _pci_device(root, "0000:00:1f.0", "0x060100")
    _pci_device(root, "0000:00:14.0", "0x0c0330")
    return root


def test_pcie_helpers():
    assert classify_pci_class("0x020000") == "NIC"
    assert classify_pci_class("0x010802") == "NVMe"
    assert classify_pci_class("0x010700") == "Storage"
    assert classify_pci_class("0x030200") == "GPU"
    assert classify_pci_class("0x120000") == classify_pci_class("0x0b4000") == "Accelerator"
    assert classify_pci_class("0x060400") == ""
    assert parse_link_speed("2.5 GT/s PCIe") == 2.5 and parse_link_speed("Unknown") == 0.0
    assert parse_aer_total("RxErr 1\nBadTLP 2\n") == 3
    assert parse_aer_total("RxErr 1\nTOTAL_ERR_COR 9\n") == 9
    assert parse_aer_total("") is None
    full = {"current_link_speed": "16.0 GT/s PCIe", "max_link_speed": "16.0 GT/s PCIe", "current_link_width": 4, "max_link_width": 4}
    assert not link_degraded(full)
    assert link_degraded({**full, "current_link_speed": "8.0 GT/s PCIe"})
    assert link_degraded({**full, "current_link_width": 2})
    assert not link_degraded({**full, "current_link_speed": "Unknown", "current_link_width": 0})


def test_pcie_enumeration_link_downgrade_and_aer(pci_sysfs):
    collector = PCIeCollector(sys_root=str(pci_sysfs))
    devices = {row["bdf"]: row for row in collector.collect()}
    assert sorted(devices) == sorted(bdf(address) for address in ("0000:3b:00.0", "0000:5e:00.0", "0000:6a:00.0", "0000:af:00.0"))

    nic = devices[bdf("0000:5e:00.0")]
    assert (nic["kind"], nic["vendor"], nic["device"], nic["class"], nic["driver"]) == ("NIC", "0x8086", "0x1592", "0x020000", "ice")
    assert nic["numa_node"] == 1 and nic["interfaces"] == ["ens1f0"]
    assert (nic["current_link_speed"], nic["max_link_speed"]) == ("8.0 GT/s PCIe", "16.0 GT/s PCIe")
    assert (nic["current_link_width"], nic["max_link_width"]) == (8, 16)
    assert nic["msi_irqs"] == ["120", "121"] and nic["msi_irq_count"] == 2
    assert (nic["aer_dev_correctable"], nic["aer_dev_nonfatal"], nic["aer_dev_fatal"], nic["aer_total"]) == (2, 0, 0, 2)
    assert nic["link_degraded"] is True and nic["aer_increasing"] is False

    nvme = devices[bdf("0000:3b:00.0")]
    assert nvme["kind"] == "NVMe" and nvme["link_degraded"] is False and nvme["aer_total"] == 0
    gpu = devices[bdf("0000:af:00.0")]
    assert gpu["kind"] == "GPU" and gpu["link_degraded"] is True and gpu["numa_node"] is None
    assert gpu["driver"] == "N/A" and gpu["aer_dev_fatal"] is None
    accelerator = devices[bdf("0000:6a:00.0")]
    assert accelerator["kind"] == "Accelerator" and accelerator["link_degraded"] is False

    write(pci_sysfs / "bus" / "pci" / "devices" / bdf("0000:5e:00.0") / "aer_dev_fatal", "TOTAL_ERR_FATAL 1\n")
    again = {row["bdf"]: row for row in collector.collect()}
    assert again[bdf("0000:5e:00.0")]["aer_total"] == 3 and again[bdf("0000:5e:00.0")]["aer_increasing"] is True
    assert again[bdf("0000:3b:00.0")]["aer_increasing"] is False
    assert PCIeCollector(sys_root=str(pci_sysfs / "missing")).collect() == []


INTERRUPTS_1 = """\
            CPU0       CPU2
  40:          3          3   IO-APIC   40-fasteoi   eth9
  50:          2          2   IO-APIC   50-fasteoi   ens1f0-legacy
  60:          9          9   IO-APIC   60-fasteoi   idle-device
 120:         10          0   IR-PCI-MSI 1048576-edge      ice-ens1f0-TxRx-0
 121:          0          5   IR-PCI-MSI 1048577-edge      ice-ens1f0-TxRx-1
 130:          1          1   IR-PCI-MSI 524288-edge      nvme0q0
 LOC:        100        100   Local timer interrupts
"""
INTERRUPTS_2 = """\
            CPU0       CPU2
  40:          5          7   IO-APIC   40-fasteoi   eth9
  50:          4          2   IO-APIC   50-fasteoi   ens1f0-legacy
  60:          9          9   IO-APIC   60-fasteoi   idle-device
 120:         30         10   IR-PCI-MSI 1048576-edge      ice-ens1f0-TxRx-0
 121:          0         25   IR-PCI-MSI 1048577-edge      ice-ens1f0-TxRx-1
 130:          5          1   IR-PCI-MSI 524288-edge      nvme0q0
 LOC:        200        300   Local timer interrupts
"""


@pytest.fixture
def irq_roots(tmp_path):
    proc, sys_root = tmp_path / "proc", tmp_path / "sys"
    write(proc / "interrupts", INTERRUPTS_1)
    write(proc / "irq" / "120" / "smp_affinity_list", "0-3\n")
    for irq in ("120", "121"):
        write(sys_root / "class" / "net" / "ens1f0" / "device" / "msi_irqs" / irq)
    write(sys_root / "class" / "net" / "ens1f0" / "device" / "numa_node", "1\n")
    (sys_root / "class" / "net" / "eth9").mkdir(parents=True)
    write(sys_root / "class" / "nvme" / "nvme0" / "device" / "msi_irqs" / "130")
    write(sys_root / "class" / "nvme" / "nvme0" / "device" / "numa_node", "0\n")
    write(sys_root / "kernel" / "irq" / "121" / "node", "0\n")
    return proc, sys_root


def test_irq_device_map_from_sysfs_msi_irqs(irq_roots):
    _, sys_root = irq_roots
    mapping, interfaces, mapped = build_irq_device_map(str(sys_root))
    assert mapping == {
        "120": {"nic": "ens1f0", "numa_node": "1", "source_class": "network"},
        "121": {"nic": "ens1f0", "numa_node": "1", "source_class": "network"},
        "130": {"nic": "", "numa_node": "0", "source_class": "storage"},
    }
    assert interfaces == ["ens1f0", "eth9"] and mapped == ["ens1f0"]
    assert build_irq_device_map(str(sys_root / "missing")) == ({}, [], [])


def test_irq_rows_use_sysfs_mapping_real_cpu_ids_and_active_filter(irq_roots):
    proc, sys_root = irq_roots
    collector = IRQCollector(proc_root=str(proc), sys_root=str(sys_root))
    assert collector.sample_rows("sut", 100.0, 1.0) == []
    write(proc / "interrupts", INTERRUPTS_2)
    rows = {row["irq"]: row for row in collector.sample_rows("sut", 101.0, 2.0)}
    assert "60" not in rows
    assert [label for label in collector.cpu_labels_seen] == ["CPU0", "CPU2"]

    queue0 = rows["120"]
    assert (queue0["nic"], queue0["source_class"], queue0["direction"], queue0["queue"]) == ("ens1f0", "network", "TxRx", "0")
    assert queue0["cpu_rates"] == {"0": 10.0, "2": 5.0} and queue0["total_rate"] == 15.0
    assert queue0["numa_node"] == "1" and queue0["affinity_list"] == "0-3"
    assert queue0["device"] == "ice-ens1f0-TxRx-0" and queue0["sut_id"] == "sut" and queue0["timestamp"] == 101.0
    assert rows["121"]["numa_node"] == "0" and rows["121"]["queue"] == "1" and rows["121"]["cpu_rates"] == {"2": 10.0}
    assert (rows["130"]["source_class"], rows["130"]["nic"], rows["130"]["numa_node"]) == ("storage", "", "0")
    assert (rows["40"]["nic"], rows["40"]["source_class"]) == ("eth9", "network")
    assert rows["50"]["nic"] == ""
    assert rows["LOC"]["source_class"] == "other" and rows["LOC"]["nic"] == ""
    ordered = [row["total_rate"] for row in collector.sample_rows("sut", 102.0, 1.0)]
    assert ordered == [0.0]


def test_irq_label_matching_without_sysfs(tmp_path):
    proc = tmp_path / "proc"
    write(proc / "interrupts", "           CPU0\n 16:         10   IO-APIC   16-fasteoi   eth0-rx-0\n")
    collector = IRQCollector(proc_root=str(proc), sys_root=str(tmp_path / "missing"))
    collector.sample_rows("sut", 1.0, 1.0)
    write(proc / "interrupts", "           CPU0\n 16:         30   IO-APIC   16-fasteoi   eth0-rx-0\n")
    row = collector.sample_rows("sut", 2.0, 1.0)[0]
    assert (row["nic"], row["source_class"], row["direction"], row["queue"], row["numa_node"]) == ("eth0", "network", "RX", "0", "N/A")


CPU_TO_NODE = {"0": 0, "1": 0, "2": 1, "3": 1}


@pytest.mark.parametrize("device_node,cpu_rates,flag,nodes,remote", [
    ("0", {"0": 60.0, "1": 40.0}, False, [0], 0.0),
    ("0", {"0": 80.0, "2": 20.0}, True, [0, 1], 20.0),
    ("0", {"0": 90.0, "2": 10.0}, False, [0], 0.0),
    ("0", {"0": 89.0, "2": 11.0}, True, [0, 1], 11.0),
    ("1", {"0": 50.0, "1": 50.0}, True, [0], 100.0),
    ("0", {"2": 5.0, "3": 5.0, "0": 90.0}, False, [0], 0.0),
    ("N/A", {"0": 50.0, "2": 50.0}, False, [0, 1], 0.0),
    ("-1", {"2": 100.0}, False, [1], 0.0),
    ("0", {"9": 100.0}, False, [], 0.0),
    ("0", {}, False, [], 0.0),
    (1, {"2": 100.0}, False, [1], 0.0),
])
def test_cross_numa_mismatch(device_node, cpu_rates, flag, nodes, remote):
    result = cross_numa_mismatch(device_node, cpu_rates, CPU_TO_NODE)
    assert result == {"cross_numa_flag": flag, "handler_numa_nodes": nodes, "remote_share_pct": remote}


def test_flatten_irq_row_has_scalar_columns_only():
    row = {
        "irq": "120", "irq_name": "ens1f0-TxRx-0", "nic": "ens1f0", "source_class": "network", "direction": "TxRx",
        "numa_node": "0", "affinity_list": "0-3", "total_rate": 100.0, "cpu_rates": {"0": 30.0, "2": 70.0, "3": 0.0},
    }
    flat = flatten_irq_row(row, CPU_TO_NODE)
    assert flat["total_irq_s"] == 100.0
    assert (flat["top_cpu"], flat["top_cpu_irq_s"], flat["active_cpu_count"]) == ("2", 70.0, 2)
    assert (flat["numa_node"], flat["handler_numa_nodes"], flat["cross_numa_flag"]) == ("0", "0,1", True)
    assert "cpu_rates" not in flat
    assert all(isinstance(value, (str, int, float, bool)) for value in flat.values())
    empty = flatten_irq_row({"irq": "1", "total_rate": 0.0}, {})
    assert (empty["top_cpu"], empty["top_cpu_irq_s"], empty["active_cpu_count"], empty["cross_numa_flag"]) == ("", 0.0, 0, False)


def test_irqbalance_status_from_proc(tmp_path):
    assert irqbalance_status(str(tmp_path / "missing")) == "unknown"
    proc = tmp_path / "proc"
    proc.mkdir()
    assert irqbalance_status(str(proc)) == "unknown"
    write(proc / "1" / "comm", "systemd\n")
    write(proc / "self" / "comm", "irqbalance\n")
    assert irqbalance_status(str(proc)) == "inactive"
    write(proc / "812" / "comm", "irqbalance\n")
    assert irqbalance_status(str(proc)) == "active"


def test_cpu_topology_uses_real_cpu_ids(tmp_path):
    cpu_root = tmp_path / "sys" / "devices" / "system" / "cpu"
    for cpu_id, node in ((0, 0), (2, 1)):
        base = cpu_root / f"cpu{cpu_id}"
        write(base / "topology" / "physical_package_id", f"{node}\n")
        write(base / "topology" / "core_id", "0\n")
        write(base / "topology" / "thread_siblings_list", f"{cpu_id}\n")
        (base / f"node{node}").mkdir()
    write(cpu_root / "cpu2" / "online", "1\n")
    write(cpu_root / "cpu2" / "cpufreq" / "scaling_cur_freq", "2400000\n")
    (cpu_root / "cpufreq").mkdir()
    rows = collect_cpu_topology("Test CPU", str(tmp_path / "sys"))
    assert [(row["cpu_id"], row["socket_id"], row["numa_node"]) for row in rows] == [(0, 0, 0), (2, 1, 1)]
    assert rows[0]["online"] is None and rows[0]["freq_khz"] is None
    assert rows[1]["online"] is True and rows[1]["freq_khz"] == 2400000 and rows[1]["cpu_model"] == "Test CPU"
    assert collect_cpu_topology("", str(tmp_path / "missing")) == []
