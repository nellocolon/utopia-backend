"""
Testa la connessione a Supabase e al database.
Uso: python scripts/test_connection.py
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def test_all():
    from app.config import get_settings
    settings = get_settings()

    print("=" * 50)
    print("UTOPIA — Connection Test")
    print("=" * 50)

    # 1. Config check
    print("\n1️⃣  Config check")
    checks = [
        ("SUPABASE_URL",             settings.supabase_url),
        ("SUPABASE_ANON_KEY",        settings.supabase_anon_key[:20] + "..." if settings.supabase_anon_key else ""),
        ("SUPABASE_SERVICE_ROLE_KEY",settings.supabase_service_role_key[:20] + "..." if settings.supabase_service_role_key else ""),
        ("DATABASE_URL",             settings.database_url[:40] + "..."),
        ("SECRET_KEY",               "✅ set" if settings.secret_key != "insecure-default-change-me" else "⚠️  using default — change this!"),
    ]
    for k, v in checks:
        icon = "✅" if v and "xxxx" not in v else "❌"
        print(f"   {icon} {k}: {v or 'NOT SET'}")

    # 2. Asyncpg connection
    print("\n2️⃣  Database connection (asyncpg)")
    try:
        import asyncpg
        conn = await asyncpg.connect(dsn=settings.database_url, timeout=10)
        version = await conn.fetchval("SELECT version()")
        print(f"   ✅ Connected: {version[:50]}")

        # Check tables
        count = await conn.fetchval("""
            SELECT COUNT(*) FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
        """)
        print(f"   ✅ Tables in public schema: {count}")

        if count == 0:
            print("   ⚠️  No tables found — run: python scripts/apply_schema.py")

        await conn.close()
    except Exception as e:
        print(f"   ❌ Failed: {e}")

    # 3. Supabase client
    print("\n3️⃣  Supabase client")
    try:
        from app.database import get_supabase
        sb = get_supabase()
        # Simple test — list tables via REST API
        resp = sb.table("users").select("id").limit(1).execute()
        print(f"   ✅ Supabase client OK")
    except Exception as e:
        print(f"   ❌ Failed: {e}")

    print("\n" + "=" * 50)
    print("Done. Fix any ❌ before starting the server.")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(test_all())
