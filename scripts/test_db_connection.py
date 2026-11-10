"""One-off smoke test: confirms GTM_DATABASE_URL is reachable and the schema applies.
Run with: .venv/Scripts/python scripts/test_db_connection.py
"""
from gtm_engine.config import load_settings
from gtm_engine.storage.database import Database


def main() -> None:
    settings = load_settings()
    if not settings.database_url:
        raise SystemExit("GTM_DATABASE_URL not set (check .env)")
    db = Database(settings.database_url)
    row = db.conn.execute("select current_database(), current_user").fetchone()
    print(f"connected: db={row['current_database']} user={row['current_user']}")
    tables = db.conn.execute(
        "select table_name from information_schema.tables where table_schema = 'public' order by 1"
    ).fetchall()
    print(f"tables ({len(tables)}): {', '.join(t['table_name'] for t in tables)}")
    db.close()
    print("OK")


if __name__ == "__main__":
    main()
