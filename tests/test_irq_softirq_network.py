from __future__ import annotations

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
