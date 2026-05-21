# Сбрасывает is_blocked=False для PDFile-записей у которых нет
# активного файла в quarantine_files (is_restored=False).
# Запускать один раз на ноутбуке 1:
#   cd C:/Users/dinar/PycharmProjects/edr
#   .venv/Scripts/python server/fix_blocked_files.py
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

_env_path = os.path.join(os.path.dirname(__file__), ".env")
from dotenv import load_dotenv
load_dotenv(_env_path)

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select, text

DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_USER = os.environ.get("DB_USER", "postgres")
DB_PASS = os.environ.get("DB_PASSWORD", "password")
DB_NAME = os.environ.get("DB_NAME", "edr_school")

DATABASE_URL = f"postgresql+asyncpg://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"


async def main():
    engine = create_async_engine(DATABASE_URL, echo=False)
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as db:
        # Находим PDFile.is_blocked=True у которых нет активного QuarantineFile
        result = await db.execute(text("""
            UPDATE pd_files
            SET is_blocked = FALSE,
                pending_quarantine = FALSE
            WHERE is_blocked = TRUE
              AND file_path NOT IN (
                  SELECT original_path
                  FROM quarantine_files
                  WHERE is_restored = FALSE
              )
        """))
        await db.commit()
        rows = result.rowcount
        print(f"Сброшено is_blocked=FALSE для {rows} файлов (без активного карантина).")

        # Показываем что осталось заблокированным (есть в карантине)
        still = await db.execute(text(
            "SELECT COUNT(*) FROM pd_files WHERE is_blocked = TRUE"
        ))
        still_count = still.scalar()
        print(f"Остаётся is_blocked=TRUE: {still_count} файлов (они действительно в карантине).")

    await engine.dispose()
    print("\nГотово.")


if __name__ == "__main__":
    asyncio.run(main())
