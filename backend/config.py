"""Application configuration loaded from environment variables with safe local defaults."""

from __future__ import annotations

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SYNCPULSE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # HTTP API Server (0.0.0.0 allows private LAN access from iPhone Safari)
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # Backup Worker TCP Socket (loopback-only by default)
    tcp_host: str = "127.0.0.1"
    tcp_port: int = 9101

    # Monitoring UDP Heartbeat (loopback-only by default)
    udp_host: str = "127.0.0.1"
    udp_port: int = 9102

    # Transfer limits & timeouts
    max_file_size_bytes: int = 50 * 1024 * 1024  # 50 MiB
    max_metadata_bytes: int = 16 * 1024  # 16 KiB
    chunk_size_bytes: int = 64 * 1024  # 64 KiB
    tcp_timeout_seconds: float = 30.0

    # UDP Heartbeat timing (experimental local defaults)
    heartbeat_interval_seconds: float = 1.0
    heartbeat_timeout_seconds: float = 4.0
    worker_id: str = "backup-worker-1"

    # Storage paths
    data_dir: str = "data"

    # Controlled fault injection for demos/tests (0 = disabled)
    udp_drop_every_n: int = 0
    tcp_throttle_ms: int = 0

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir).resolve()

    @property
    def staging_dir(self) -> Path:
        return self.data_path / "staging"

    @property
    def backups_dir(self) -> Path:
        return self.data_path / "backups"

    @property
    def db_path(self) -> Path:
        return self.data_path / "syncpulse.db"

    def ensure_directories(self) -> None:
        self.data_path.mkdir(parents=True, exist_ok=True)
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        self.backups_dir.mkdir(parents=True, exist_ok=True)


def get_settings() -> Settings:
    return Settings()
