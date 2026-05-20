"""
Добавляет новые колонки в существующую БД БЕЗ удаления данных.
Запускать один раз после git pull на ноутбуке 1:

    cd C:\Users\dinar\PycharmProjects\edr
    .venv\Scripts\python server\add_columns.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

_env_path = os.path.join(os.path.dirname(__file__), ".env")
from dotenv import load_dotenv
load_dotenv(_env_path)

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_USER = os.environ.get("DB_USER", "postgres")
DB_PASS = os.environ.get("DB_PASSWORD", "password")
DB_NAME = os.environ.get("DB_NAME", "edr_school")

DATABASE_URL = f"postgresql+asyncpg://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"


async def main():
    engine = create_async_engine(DATABASE_URL, echo=False)

    migrations = [
        # pd_files
        "ALTER TABLE pd_files ADD COLUMN IF NOT EXISTS pending_quarantine BOOLEAN DEFAULT FALSE",
        # quarantine_files
        "ALTER TABLE quarantine_files ADD COLUMN IF NOT EXISTS pending_restore BOOLEAN DEFAULT FALSE",
    ]

    async with engine.begin() as conn:
        for sql in migrations:
            await conn.execute(text(sql))
            table = sql.split("TABLE")[1].split("ADD")[0].strip()
            col   = sql.split("COLUMN IF NOT EXISTS")[1].split()[0].strip()
            print(f"  OK: {table}.{col}")

    await engine.dispose()
    print("\nГотово — данные не удалены.")


if __name__ == "__main__":
    asyncio.run(main())
