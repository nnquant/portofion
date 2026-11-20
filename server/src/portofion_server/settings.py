from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.requests import Request

DEFAULT_WORKSPACE_SETTINGS = {
    "periods_per_year": 252,
    "rf": 0.0,
    "var_level": 0.95,
    "outlier_abs": 0.5,
    "stale_days": 7,
    "up_color": "red",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PORTOFION_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://portofion:portofion@127.0.0.1:55432/portofion"
    secret_key: str = "dev-secret-change-me"
    base_url: str = "http://localhost:8000"
    trusted_origins: str = ""  # extra comma-separated origins allowed for cookie-authenticated writes
    allow_signup: bool = False
    cookie_secure: bool = False
    session_days: int = 30
    max_body_mb: int = 8
    max_points_per_series: int = 20_000
    max_points_per_request: int = 200_000
    max_series_per_item: int = 50
    bootstrap_admin_email: str | None = None
    bootstrap_admin_password: str | None = None
    static_dir: str | None = None
    sdk_dir: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()


def public_base(request: Request) -> str:
    """URL agents and links should use: PORTOFION_BASE_URL when set, else the URL this request came in on."""
    cfg = get_settings()
    if "base_url" in cfg.model_fields_set:
        return cfg.base_url.rstrip("/")
    h = request.headers
    host = (h.get("x-forwarded-host") or h.get("host") or "").split(",")[0].strip()
    proto = (h.get("x-forwarded-proto") or request.url.scheme).split(",")[0].strip()
    return f"{proto}://{host}" if host else cfg.base_url.rstrip("/")
