from __future__ import annotations
from functools import lru_cache
from supabase import create_client, Client
from supabase.lib.client_options import ClientOptions
import asyncpg
from app.config import get_settings

settings = get_settings()


@lru_cache()
def get_supabase() -> Client:
    return create_client(settings.supabase_url, settings.supabase_anon_key)


@lru_cache()
def get_supabase_admin() -> Client:
    return create_client(
        settings.supabase_url,
        settings.supabase_service_role_key,
        options=ClientOptions(auto_refresh_token=False, persist_session=False),
    )


_pool = None  # asyncpg.Pool


async def get_db_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=settings.database_url,
            min_size=2,
            max_size=10,
            command_timeout=30,
        )
    return _pool


async def close_db_pool():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


async def get_db():
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        yield conn
