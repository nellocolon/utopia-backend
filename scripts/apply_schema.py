"""
Script di utility per applicare lo schema SQL su Supabase.
Uso: python scripts/apply_schema.py

Alternativa: incollare utopia_schema.sql direttamente nel SQL Editor di Supabase.
"""
import asyncio
import asyncpg
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import get_settings

SCHEMA_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "utopia_schema.sql")


async def apply_schema():
    settings = get_settings()

    if not os.path.exists(SCHEMA_FILE):
        print(f"❌ Schema file not found: {SCHEMA_FILE}")
        print("   Assicurati che utopia_schema.sql sia nella root del progetto.")
        sys.exit(1)

    with open(SCHEMA_FILE, "r") as f:
        sql = f.read()

    print(f"📂 Schema file: {SCHEMA_FILE}")
    print(f"🔗 Connecting to: {settings.database_url[:40]}...")

    try:
        conn = await asyncpg.connect(dsn=settings.database_url)
        print("✅ Connected to database")

        print("⚙️  Applying schema...")
        await conn.execute(sql)
        print("✅ Schema applied successfully!")

        # Quick verification
        tables = await conn.fetch("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            ORDER BY table_name
        """)
        print(f"\n📋 Tables created ({len(tables)}):")
        for t in tables:
            print(f"   • {t['table_name']}")

        await conn.close()

    except asyncpg.PostgresError as e:
        print(f"❌ Database error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(apply_schema())
