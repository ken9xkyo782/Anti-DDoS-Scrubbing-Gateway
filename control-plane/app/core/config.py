from functools import lru_cache

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
    cookie_samesite: str = "lax"

    bootstrap_admin_username: str | None = None
    bootstrap_admin_password: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
