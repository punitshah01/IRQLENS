from __future__ import annotations

import os
from pathlib import Path
from typing import List


def _env(name: str, default: str) -> str:
    return os.getenv(name, default)


class Settings:
    def __init__(self) -> None:
        self.db_path: Path = Path(_env("IRQLENS_DB_PATH", str(Path(__file__).resolve().parents[1] / "data" / "irqlens.db"))).resolve()
        self.metric_retention: int = int(_env("IRQLENS_METRIC_RETENTION", "5000"))
        self.disable_ingest_allowlist: bool = _env("IRQLENS_DISABLE_INGEST_ALLOWLIST", "0") in ("1", "true", "True", "yes")
        cors = _env("IRQLENS_CORS_ORIGINS", "*")
        self.cors_origins: List[str] = [x.strip() for x in cors.split(",") if x.strip()] if cors else ["*"]
        allow = _env("IRQLENS_ALLOWED_INGEST_IPS", "")
        self.allowed_ingest_ips: List[str] = [x.strip() for x in allow.split(",") if x.strip()]

    def ensure_dirs(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)


settings = Settings()
