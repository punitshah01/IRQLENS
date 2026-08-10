from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List


def _env(name: str, default: str) -> str:
    return os.getenv(name, default)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw in ("1", "true", "True", "yes", "on")


def _env_list(name: str, default: str) -> List[str]:
    raw = _env(name, default)
    return [x.strip() for x in raw.split(",") if x.strip()]


@dataclass
class Settings:
    bind_host: str
    bind_port: int
    db_path: Path
    output_dir: Path
    log_level: str
    collection_interval: float
    metric_retention: int
    retention_days: int
    max_sessions: int
    max_storage_mb: int
    command_timeout_seconds: float
    disable_ingest_allowlist: bool
    cors_origins: List[str]
    allowed_ingest_ips: List[str]
    enabled_intervals: List[float]

    @classmethod
    def from_env(cls) -> "Settings":
        bind_host = _env("IRQLENS_HOST", "0.0.0.0")
        bind_port = _env_int("IRQLENS_PORT", 8080)
        db_path = Path(_env("IRQLENS_DB_PATH", str(Path(__file__).resolve().parents[1] / "data" / "irqlens.db"))).resolve()
        output_dir = Path(_env("IRQLENS_OUTPUT_DIR", "/root/irqlens")).resolve()
        log_level = _env("IRQLENS_LOG_LEVEL", "INFO")
        collection_interval = _env_float("IRQLENS_COLLECTION_INTERVAL", 1.0)
        metric_retention = _env_int("IRQLENS_METRIC_RETENTION", 5000)
        retention_days = _env_int("IRQLENS_RETENTION_DAYS", 14)
        max_sessions = _env_int("IRQLENS_MAX_SESSIONS", 100)
        max_storage_mb = _env_int("IRQLENS_MAX_STORAGE_MB", 1024)
        command_timeout_seconds = _env_float("IRQLENS_COMMAND_TIMEOUT_SECONDS", 8.0)
        disable_ingest_allowlist = _env_bool("IRQLENS_DISABLE_INGEST_ALLOWLIST", False)

        cors_raw = _env("IRQLENS_CORS_ORIGINS", "*")
        cors_origins = [x.strip() for x in cors_raw.split(",") if x.strip()] if cors_raw else ["*"]
        allowed_ingest_ips = _env_list("IRQLENS_ALLOWED_INGEST_IPS", "")

        interval_raw = _env_list("IRQLENS_SUPPORTED_INTERVALS", "0.1,0.25,0.5,1,2,5,10")
        enabled_intervals: List[float] = []
        for item in interval_raw:
            try:
                val = float(item)
            except ValueError:
                continue
            if val > 0:
                enabled_intervals.append(val)
        if not enabled_intervals:
            enabled_intervals = [0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0]

        return cls(
            bind_host=bind_host,
            bind_port=bind_port,
            db_path=db_path,
            output_dir=output_dir,
            log_level=log_level,
            collection_interval=max(0.1, collection_interval),
            metric_retention=max(100, metric_retention),
            retention_days=max(1, retention_days),
            max_sessions=max(1, max_sessions),
            max_storage_mb=max(128, max_storage_mb),
            command_timeout_seconds=max(1.0, command_timeout_seconds),
            disable_ingest_allowlist=disable_ingest_allowlist,
            cors_origins=cors_origins,
            allowed_ingest_ips=allowed_ingest_ips,
            enabled_intervals=sorted(set(enabled_intervals)),
        )

    def ensure_dirs(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / "sessions").mkdir(parents=True, exist_ok=True)
        (self.output_dir / "latest").mkdir(parents=True, exist_ok=True)


settings = Settings.from_env()
