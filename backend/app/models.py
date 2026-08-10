from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class AppDependencyStatus(BaseModel):
    name: str
    available: bool
    detail: str = ""


class SystemInfo(BaseModel):
    timestamp: float
    hostname: str
    kernel: str
    os_distribution: str
    os_version: str
    uptime_seconds: float
    boot_time_epoch: float
    loadavg_1m: float
    loadavg_5m: float
    loadavg_15m: float
    cpu_count: int
    cpu_model: str
    memory_total_kb: int
    memory_available_kb: int
    numa_nodes: int
    running_as_root: bool
    architecture: str = "N/A"


class InterfaceInfo(BaseModel):
    timestamp: float
    name: str
    state: str
    mtu: Optional[int] = None
    mac: str = "N/A"
    speed_mbps: Optional[int] = None
    duplex: str = "N/A"
    driver: str = "N/A"
    firmware: str = "N/A"
    ipv4: List[str] = Field(default_factory=list)
    ipv6: List[str] = Field(default_factory=list)


class IRQRate(BaseModel):
    cpu: str
    rate: float


class IRQSample(BaseModel):
    timestamp: float
    sut_ip: str
    sut_id: str = ""
    irq: str
    irq_name: str
    device: str = "N/A"
    interrupt_type: str = "N/A"
    affinity_list: str = "N/A"
    numa_node: str = "N/A"
    nic: str = ""
    queue: str = ""
    direction: str = "Other"
    source_class: str = "other"
    total_count: int = 0
    total_rate: float
    cpu_rates: Dict[str, float] = Field(default_factory=dict)


class SoftIRQSample(BaseModel):
    timestamp: float
    sut_ip: str
    sut_id: str = ""
    totals: Dict[str, int] = Field(default_factory=dict)
    rates: Dict[str, float] = Field(default_factory=dict)
    per_cpu_rates: Dict[str, float] = Field(default_factory=dict)


class NetworkSample(BaseModel):
    timestamp: float
    sut_ip: str
    sut_id: str = ""
    interface: str
    rx_bytes: int = 0
    tx_bytes: int = 0
    rx_packets: int = 0
    tx_packets: int = 0
    rx_errors: int = 0
    tx_errors: int = 0
    rx_drops: int = 0
    tx_drops: int = 0
    rx_bps: float = 0.0
    tx_bps: float = 0.0
    rx_pps: float = 0.0
    tx_pps: float = 0.0
    rx_err_ps: float = 0.0
    tx_err_ps: float = 0.0
    rx_drop_ps: float = 0.0
    tx_drop_ps: float = 0.0


class NetworkCorrelation(BaseModel):
    timestamp: float
    interface: str
    rx_irqs: int
    tx_irqs: int
    rx_irq_rate: float
    tx_irq_rate: float
    rx_pps: float
    tx_pps: float
    rx_bps: float
    tx_bps: float
    correlation_available: bool


class DiagnosticCommandResult(BaseModel):
    timestamp: float
    category: str
    command: str
    interface: str = ""
    exit_code: int
    stdout: str
    stderr: str
    success: bool
    duration_ms: float


class CollectionSession(BaseModel):
    session_id: str
    sut_id: str = ""
    status: Literal["running", "stopped", "failed"]
    start_time: float
    end_time: Optional[float] = None
    hostname: str
    os_distribution: str
    kernel: str
    collector_version: str
    output_dir: str
    categories: List[str] = Field(default_factory=list)
    error: str = ""


class ExportFile(BaseModel):
    name: str
    category: str
    format: Literal["json", "csv", "xml", "txt", "zip"]
    path: str
    size_bytes: int


class HealthStatus(BaseModel):
    ok: bool
    application: str
    collector_status: str
    database_status: str
    websocket_status: str
    hostname: str
    os_distribution: str
    kernel: str
    uptime_seconds: float
    running_as_root: bool
    dependencies: List[AppDependencyStatus] = Field(default_factory=list)
    interval_seconds: float


class IngestPayload(BaseModel):
    samples: List[IRQSample] = Field(default_factory=list)
    host_samples: List[NetworkSample] = Field(default_factory=list)


class DashboardSnapshot(BaseModel):
    timestamp: float
    system: SystemInfo
    irq_summary: Dict[str, Any]
    irq_rows: List[IRQSample]
    softirq: SoftIRQSample
    network_global: Dict[str, float]
    interfaces: List[InterfaceInfo]
    network_samples: List[NetworkSample]
    network_correlation: List[NetworkCorrelation]


class SessionStartRequest(BaseModel):
    categories: List[str] = Field(default_factory=lambda: ["irq", "softirq", "network", "interfaces", "routes", "sockets", "ethtool", "system"])
    sut_id: str = ""


class SessionStopRequest(BaseModel):
    reason: str = "manual"


class SessionProgress(BaseModel):
    session_id: str
    status: str
    started: float
    updated: float
    progress_percent: float
    current_step: str


class LogEntry(BaseModel):
    timestamp: float
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"]
    component: str
    message: str


class SystemRecord(BaseModel):
    id: str
    name: str
    hostname: str
    address: str
    port: int
    os_distribution: str
    os_version: str
    kernel: str
    architecture: str
    agent_version: str
    status: Literal["ONLINE", "OFFLINE", "CONNECTING", "STALE", "ERROR"]
    last_seen: float
    created_at: float
    updated_at: float
    cpu_count: int = 0
    cpu_model: str = ""
    memory_total_kb: int = 0
    numa_nodes: int = 0
    interfaces: List[str] = Field(default_factory=list)
    ip_addresses: List[str] = Field(default_factory=list)
    mode: Literal["local", "remote"] = "remote"


class CPUTopologyEntry(BaseModel):
    cpu_id: int
    socket_id: Optional[int] = None
    core_id: Optional[int] = None
    numa_node: Optional[int] = None
    online: Optional[bool] = None
    thread_siblings_list: str = ""
    core_siblings_list: str = ""
    cpu_model: str = ""


class SystemCreateRequest(BaseModel):
    id: str
    name: str
    address: str
    port: int = 8443
    token: str = ""


class AgentRegistrationRequest(BaseModel):
    sut_id: str
    name: str
    address: str
    port: int = 8443
    token_hint: str = ""
    agent_version: str
    telemetry_interval: float = 1.0
    hostname: str
    os_distribution: str
    os_version: str
    kernel: str
    architecture: str
    cpu_count: int
    cpu_model: str
    memory_total_kb: int
    numa_nodes: int
    uptime_seconds: float
    interfaces: List[str] = Field(default_factory=list)
    ip_addresses: List[str] = Field(default_factory=list)
    cpu_topology: List[CPUTopologyEntry] = Field(default_factory=list)


class AgentHeartbeatRequest(BaseModel):
    sut_id: str
    agent_version: str
    uptime_seconds: float
    timestamp: float


class AgentTelemetryPayload(BaseModel):
    type: Literal["telemetry"] = "telemetry"
    sut_id: str
    timestamp: float
    system: SystemInfo
    irq_rows: List[IRQSample]
    softirq: SoftIRQSample
    network_samples: List[NetworkSample]
    interfaces: List[InterfaceInfo]
    irq_summary: Dict[str, Any] = Field(default_factory=dict)
    network_global: Dict[str, float] = Field(default_factory=dict)
    cpu_topology: List[CPUTopologyEntry] = Field(default_factory=list)
