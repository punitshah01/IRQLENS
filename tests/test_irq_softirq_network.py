from __future__ import annotations

import json
import subprocess
from unittest.mock import Mock
import pytest

from backend.app.collectors.irq import IRQCollector
from backend.app.collectors.network import NetworkCollector
from backend.app.collectors.softirq import SoftIRQCollector


def test_irq_parse_and_rate(monkeypatch):
    sample1 = """           CPU0       CPU1
 16:         10          5   IO-APIC   16-fasteoi   eth0-rx-0
 17:         20         10   IO-APIC   17-fasteoi   eth0-tx-0
"""
    sample2 = """           CPU0       CPU1
 16:         30         15   IO-APIC   16-fasteoi   eth0-rx-0
 17:         22         12   IO-APIC   17-fasteoi   eth0-tx-0
"""

    seq = [sample1, sample2]

    def fake_read(_path: str):
        return seq.pop(0)

    monkeypatch.setattr("backend.app.collectors.irq.read_text_safe", fake_read)
    c = IRQCollector()
    _, rows1 = c.parse()
    rates1 = c.rates(rows1, 1.0)
    assert rates1 == {}

    _, rows2 = c.parse()
    rates2 = c.rates(rows2, 2.0)
    assert "16" in rates2
    assert abs(rates2["16"]["total_rate"] - 15.0) < 1e-6


def test_softirq_parse_and_rate(monkeypatch):
    sample1 = """                    CPU0       CPU1
NET_RX:              100        200
NET_TX:               10         20
"""
    sample2 = """                    CPU0       CPU1
NET_RX:              130        230
NET_TX:               40         30
"""

    seq = [sample1, sample2]

    def fake_read(_path: str):
        return seq.pop(0)

    monkeypatch.setattr("backend.app.collectors.softirq.read_text_safe", fake_read)
    c = SoftIRQCollector()
    totals1, per_cpu1 = c.parse()
    rates1, cpu_rates1 = c.rates(totals1, per_cpu1, 1.0)
    assert rates1 == {}

    totals2, per_cpu2 = c.parse()
    rates2, cpu_rates2 = c.rates(totals2, per_cpu2, 2.0)
    assert abs(rates2["NET_RX"] - 30.0) < 1e-6
    assert "0" in cpu_rates2


def test_network_interface_discovery(monkeypatch):
    sample = """Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
  lo: 1000 10 0 0 0 0 0 0 1000 10 0 0 0 0 0 0
eth0: 2000 20 0 1 0 0 0 0 3000 30 0 2 0 0 0 0
veth0: 500 5 0 0 0 0 0 0 600 6 0 0 0 0 0 0
"""

    monkeypatch.setattr("backend.app.collectors.network.read_text_safe", lambda _path: sample)
    c = NetworkCollector()
    interfaces = c.discover_interfaces()
    assert "lo" in interfaces
    assert "eth0" in interfaces
    assert "veth0" in interfaces


def test_interface_addresses_and_cache(monkeypatch):
    payload = [{"ifname": "eth0", "addr_info": [
        {"family": "inet", "local": "192.0.2.10", "prefixlen": 24},
        {"family": "inet6", "local": "2001:db8::10", "prefixlen": 64},
        {"local": "invalid", "prefixlen": 24},
    ]}]
    run = Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps(payload), ""))
    monkeypatch.setattr("backend.app.collectors.network.subprocess.run", run)
    collector = NetworkCollector()
    collector.collect(1.0, 100.0)
    collector.collect(1.0, 101.0)
    info = collector._iface_info("eth0", 101.0)
    assert info["ipv4"] == ["192.0.2.10/24"]
    assert info["ipv6"] == ["2001:db8::10/64"]
    run.assert_called_once()


def test_interface_addresses_without_ip_tool(monkeypatch):
    monkeypatch.setattr("backend.app.collectors.network.subprocess.run", Mock(side_effect=FileNotFoundError))
    collector = NetworkCollector()
    collector.collect(1.0, 100.0)
    assert collector._iface_info("eth0", 100.0)["ipv4"] == []


@pytest.mark.parametrize("output", ["not-json", "{}", '[{"ifname":"eth0","addr_info":null}]'])
def test_interface_addresses_malformed_output(monkeypatch, output):
    monkeypatch.setattr("backend.app.collectors.network.subprocess.run", Mock(return_value=subprocess.CompletedProcess([], 0, output, "")))
    collector = NetworkCollector()
    collector.collect(1.0, 100.0)
    assert collector._iface_info("eth0", 100.0)["ipv4"] == []


def test_diagnostic_timeout_decodes_output(monkeypatch):
    from backend.app.collectors.commands import DiagnosticCommandCollector

    monkeypatch.setattr("backend.app.collectors.commands.subprocess.run", Mock(side_effect=subprocess.TimeoutExpired(
        ["ethtool", "eth0"], 1, output=b"partial output", stderr=b"partial error",
    )))
    result = DiagnosticCommandCollector(1).run("ethtool", ["ethtool", "eth0"])
    assert result.exit_code == 124
    assert not result.success
    assert result.stdout == "partial output"
    assert "partial error" in result.stderr
