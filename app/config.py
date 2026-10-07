from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic_settings import BaseSettings, SettingsConfigDict


def to_async_url(url: str) -> str:
    """Accept plain postgres URLs (as EasyPanel provides them) and force the asyncpg driver.

    asyncpg rejects libpq's `sslmode=` (EasyPanel adds `?sslmode=disable`); it takes `ssl=` with the same values.
    """
    url = url.strip()
    if url.startswith("sqlite"):
        return url
    for prefix in ("postgres://", "postgresql://", "postgresql+psycopg2://"):
        if url.startswith(prefix):
            url = "postgresql+asyncpg://" + url[len(prefix):]
            break
    if url.startswith("postgresql+asyncpg://"):
        parts = urlsplit(url)
        query = [("ssl" if k == "sslmode" else k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)]
        url = urlunsplit(parts._replace(query=urlencode(query)))
    return url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://postgres:postgres@localhost:5432/qalbalkhalij"
    cors_origins: str = "https://qaalbalkhalij.store"
    site_url: str = "https://qaalbalkhalij.store"

    maxmind_account_id: str = ""
    maxmind_license_key: str = ""
    # Refuse orders when MaxMind can't give a verdict (strict). true = let them through instead.
    maxmind_fail_open: bool = False
    # Block IPs whose MaxMind ip_risk_snapshot (0.01-99) is at or above this.
    maxmind_max_risk: float = 20
    maxmind_timeout_seconds: float = 5

    sheets_webhook_url: str = ""

    fb_pixel_id: str = ""
    fb_access_token: str = ""

    tiktok_pixel_id: str = ""
    tiktok_access_token: str = ""

    snap_pixel_id: str = ""
    snap_access_token: str = ""

    whitelisted_phones: str = "0550000000"

    order_rate_limit_per_ip: int = 10
    order_rate_limit_per_phone: int = 3

    @property
    def async_database_url(self) -> str:
        return to_async_url(self.database_url)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def whitelist(self) -> set[str]:
        return {p.strip() for p in self.whitelisted_phones.split(",") if p.strip()}


settings = Settings()
