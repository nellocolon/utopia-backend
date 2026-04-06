from __future__ import annotations
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache
from typing import List


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    app_env: str = "development"
    secret_key: str = "insecure-default-change-me"
    debug: bool = False
    allowed_origins: str = "http://localhost:5173"
    port: int = 8000

    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""
    database_url: str = ""

    x_client_id: str = ""
    x_client_secret: str = ""
    x_callback_url: str = "http://localhost:8000/auth/x/callback"

    socialdata_api_key: str = ""
    socialdata_base_url: str = "https://api.socialdata.tools"

    redis_url: str = "redis://localhost:6379/0"

    solana_rpc_url: str = "https://api.mainnet-beta.solana.com"
    solana_commitment: str = "confirmed"

    offertoro_postback_secret: str = ""
    adgate_postback_secret: str = ""
    freecash_postback_secret: str = ""

    sentry_dsn: str = ""

    @property
    def cors_origins(self) -> List[str]:
        return [o.strip() for o in self.allowed_origins.split(",")]

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
