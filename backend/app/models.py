from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Any, Dict, List


class IrqSample(BaseModel):
    timestamp: float
    sut_ip: str
    irq: str
    irq_name: str
    nic: str = ""
    queue: str = ""
    direction: str = "Other"
    source_class: str = "other"
    total_rate: float
    cpu_rates: Dict[str, float] = Field(default_factory=dict)
    affinity_list: str = ""


class HostSample(BaseModel):
    timestamp: float
    sut_ip: str
    nic: str = ""
    rx_bps: float = 0.0
    tx_bps: float = 0.0
    rx_pps: float = 0.0
    tx_pps: float = 0.0
    rx_drop_ps: float = 0.0
    tx_drop_ps: float = 0.0
    softirq_rates: Dict[str, float] = Field(default_factory=dict)
    details: Dict[str, Any] = Field(default_factory=dict)


class IngestPayload(BaseModel):
    samples: List[IrqSample] = Field(default_factory=list)
    host_samples: List[HostSample] = Field(default_factory=list)
