from decimal import Decimal
from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CONTROL_PLANE_",
        env_file=".env",
        extra="ignore",
    )

    app_name: str = "Anti-DDoS Control Plane"
    database_url: str = (
        "postgresql+asyncpg://control_plane:control_plane@127.0.0.1:55432/control_plane_test"
    )
    redis_url: str = "redis://127.0.0.1:56379/0"

    session_cookie_name: str = "control_plane_session"
    session_idle_seconds: int = Field(default=30 * 60, gt=0)
    session_absolute_seconds: int = Field(default=12 * 60 * 60, gt=0)
    cookie_secure: bool = True
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"

    bootstrap_admin_username: str | None = None
    bootstrap_admin_password: str | None = None
    node_clean_capacity_gbps: Decimal = Field(default=Decimal("40"), gt=0)

    worker_poll_timeout_seconds: float = Field(default=2.0, gt=0)
    worker_reconcile_interval_seconds: float = Field(default=15.0, gt=0)
    worker_backoff_initial_seconds: float = Field(default=0.5, gt=0)
    worker_backoff_max_seconds: float = Field(default=30.0, gt=0)
    worker_shutdown_grace_seconds: float = Field(default=10.0, gt=0)
    worker_apply_binary_path: str = "../data-plane/build/xdpgw-apply"
    worker_apply_timeout_seconds: float = Field(default=5.0, gt=0)
    worker_telemetry_enabled: bool = True
    worker_telemetry_interval_seconds: int = Field(default=2, ge=1, le=2)
    worker_telemetry_retention_seconds: int = Field(default=7 * 24 * 60 * 60, gt=0)
    worker_telemetry_binary_path: str = "../data-plane/build/dpstat"
    worker_telemetry_ifindex: int | None = Field(default=None, ge=0)
    worker_telemetry_timeout_seconds: float = Field(default=5.0, gt=0)

    feed_fetch_connect_timeout_seconds: float = Field(default=5.0, gt=0)
    feed_fetch_read_timeout_seconds: float = Field(default=10.0, gt=0)
    feed_fetch_write_timeout_seconds: float = Field(default=5.0, gt=0)
    feed_fetch_pool_timeout_seconds: float = Field(default=5.0, gt=0)
    feed_fetch_wall_timeout_seconds: float = Field(default=30.0, gt=0)
    feed_fetch_max_decoded_body_bytes: int = Field(default=32 * 1024 * 1024, gt=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
