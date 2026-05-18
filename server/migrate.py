"""
Создаёт базу данных edr_school и все таблицы.

Запуск:
    cd C:/Users/dinar/PycharmProjects/edr
    .venv/Scripts/python server/migrate.py
"""
import asyncio
import os
import sys

from dotenv import load_dotenv

# Загружаем переменные из server/.env
_env_path = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(_env_path)

# Целевая БД
TARGET_DB = "edr_school"
os.environ["DB_NAME"] = TARGET_DB           # db.py прочитает это значение

DB_HOST     = os.environ.get("DB_HOST", "localhost")
DB_PORT     = int(os.environ.get("DB_PORT", "5432"))
DB_USER     = os.environ.get("DB_USER", "postgres")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "password")

# Чтобы db.py импортировался корректно
sys.path.insert(0, os.path.dirname(__file__))


# ── Шаг 1: создать БД ────────────────────────────────────────────────────────

async def create_database():
    import asyncpg

    print(f"[1/3] Подключаемся к PostgreSQL {DB_HOST}:{DB_PORT} (пользователь: {DB_USER})")
    conn = await asyncpg.connect(
        host=DB_HOST, port=DB_PORT,
        user=DB_USER, password=DB_PASSWORD,
        database="postgres",
    )
    try:
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", TARGET_DB
        )
        if exists:
            print(f"[OK] База данных '{TARGET_DB}' уже существует — пропускаем создание.")
        else:
            await conn.execute(
                f"CREATE DATABASE \"{TARGET_DB}\" ENCODING 'UTF8' "
                f"LC_COLLATE 'Russian_Russia.1251' LC_CTYPE 'Russian_Russia.1251' "
                f"TEMPLATE template0"
            )
            print(f"[OK] База данных '{TARGET_DB}' создана.")
    except Exception as e:
        # Если локаль недоступна — пробуем без неё
        if "invalid locale" in str(e).lower() or "locale" in str(e).lower():
            await conn.execute(f'CREATE DATABASE "{TARGET_DB}"')
            print(f"[OK] База данных '{TARGET_DB}' создана (без явной локали).")
        else:
            raise
    finally:
        await conn.close()


# ── Шаг 2: создать таблицы ───────────────────────────────────────────────────

async def create_tables():
    from db import Base, engine
    import models  # noqa: регистрирует все модели в Base.metadata

    print(f"[2/3] Пересоздаём таблицы в '{TARGET_DB}'...")

    async with engine.begin() as conn:
        # Полная зачистка схемы — удаляет ВСЕ таблицы, включая старые
        await conn.execute(
            __import__("sqlalchemy").text("DROP SCHEMA public CASCADE")
        )
        await conn.execute(
            __import__("sqlalchemy").text("CREATE SCHEMA public")
        )
        await conn.execute(
            __import__("sqlalchemy").text("GRANT ALL ON SCHEMA public TO postgres")
        )
        await conn.execute(
            __import__("sqlalchemy").text("GRANT ALL ON SCHEMA public TO public")
        )
        print("[OK] Схема public пересоздана.")
        # Создаём таблицы по актуальным моделям
        await conn.run_sync(Base.metadata.create_all)

    print("[OK] Таблицы созданы по актуальной схеме.")


# ── Шаг 3: проверить результат ───────────────────────────────────────────────

async def verify():
    import asyncpg
    import sqlalchemy

    print("[3/3] Проверяем схему базы данных...")

    conn = await asyncpg.connect(
        host=DB_HOST, port=DB_PORT,
        user=DB_USER, password=DB_PASSWORD,
        database=TARGET_DB,
    )
    try:
        rows = await conn.fetch(
            """
            SELECT
                t.table_name,
                COUNT(c.column_name) AS columns
            FROM information_schema.tables t
            JOIN information_schema.columns c
                ON c.table_name = t.table_name
               AND c.table_schema = t.table_schema
            WHERE t.table_schema = 'public'
              AND t.table_type = 'BASE TABLE'
            GROUP BY t.table_name
            ORDER BY t.table_name
            """
        )

        if not rows:
            print("[WARN] Таблицы не обнаружены.")
            return

        print(f"\n{'Таблица':<20} {'Колонок':>8}")
        print("-" * 30)
        for row in rows:
            print(f"  {row['table_name']:<18} {row['columns']:>6}")
        print("-" * 30)
        print(f"  Итого таблиц: {len(rows)}")

        # Выводим колонки каждой таблицы
        print()
        for row in rows:
            table = row["table_name"]
            cols = await conn.fetch(
                """
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = $1
                ORDER BY ordinal_position
                """,
                table,
            )
            col_str = ", ".join(c["column_name"] for c in cols)
            print(f"  {table}: {col_str}")

    finally:
        await conn.close()


# ── Точка входа ───────────────────────────────────────────────────────────────

async def main():
    print("=" * 50)
    print("  EDR School — миграция базы данных")
    print("=" * 50)

    await create_database()
    await create_tables()
    await verify()

    print("\n[DONE] Все миграции выполнены успешно.")
    print(f"       Подключение: postgresql://{DB_USER}@{DB_HOST}:{DB_PORT}/{TARGET_DB}")


if __name__ == "__main__":
    asyncio.run(main())
