"""Хранилище бота: профиль, история диалога, факты, добытые из разговора.

Три таблицы вместо одной, потому что у них разная жизнь: профиль правится
редко и целиком, история пишется на каждое сообщение и читается окном, факты
приходят по одному и должны перезаписываться по ключу.
"""
import os

import asyncpg

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    tg_id      BIGINT PRIMARY KEY,
    name       TEXT,
    goal       TEXT,
    level      TEXT,
    age        INT,
    limits     TEXT,
    equipment  TEXT,
    freq       TEXT,
    last_why   TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS messages (
    id         BIGSERIAL PRIMARY KEY,
    tg_id      BIGINT NOT NULL,
    role       TEXT NOT NULL,
    text       TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS messages_tg_idx ON messages (tg_id, id DESC);
CREATE TABLE IF NOT EXISTS facts (
    tg_id      BIGINT NOT NULL,
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (tg_id, key)
);
"""

PROFILE_FIELDS = ("name", "goal", "level", "age", "limits", "equipment", "freq")
_pool: asyncpg.Pool | None = None


async def pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=4)
        async with _pool.acquire() as c:
            await c.execute(SCHEMA)
    return _pool


async def get_profile(tg_id: int) -> dict:
    p = await pool()
    async with p.acquire() as c:
        row = await c.fetchrow("SELECT * FROM users WHERE tg_id = $1", tg_id)
        facts = await c.fetch("SELECT key, value FROM facts WHERE tg_id = $1 ORDER BY created_at", tg_id)
    prof = dict(row) if row else {}
    prof["facts"] = {f["key"]: f["value"] for f in facts}
    return prof


async def save_profile(tg_id: int, **fields) -> None:
    """Профиль пишется целиком после анкеты и точечно при правках."""
    fields = {k: v for k, v in fields.items() if k in PROFILE_FIELDS}
    cols = ", ".join(fields)
    ph = ", ".join(f"${i + 2}" for i in range(len(fields)))
    upd = ", ".join(f"{k} = EXCLUDED.{k}" for k in fields)
    p = await pool()
    async with p.acquire() as c:
        await c.execute(
            f"INSERT INTO users (tg_id, {cols}) VALUES ($1, {ph}) "
            f"ON CONFLICT (tg_id) DO UPDATE SET {upd}",
            tg_id, *fields.values())


async def add_message(tg_id: int, role: str, text: str) -> None:
    p = await pool()
    async with p.acquire() as c:
        await c.execute("INSERT INTO messages (tg_id, role, text) VALUES ($1, $2, $3)",
                        tg_id, role, text[:4000])


async def history(tg_id: int, limit: int = 12) -> list[dict]:
    """Окно последних сообщений в хронологическом порядке.

    Берём с конца, потому что важен свежий контекст, а разворачиваем обратно,
    потому что модели нужен нормальный ход разговора, а не задом наперёд."""
    p = await pool()
    async with p.acquire() as c:
        rows = await c.fetch(
            "SELECT role, text FROM messages WHERE tg_id = $1 ORDER BY id DESC LIMIT $2",
            tg_id, limit)
    return [dict(r) for r in reversed(rows)]


async def save_fact(tg_id: int, key: str, value: str) -> None:
    """Факт из живой реплики. По ключу перезаписываем: человек мог передумать."""
    p = await pool()
    async with p.acquire() as c:
        await c.execute(
            "INSERT INTO facts (tg_id, key, value) VALUES ($1, $2, $3) "
            "ON CONFLICT (tg_id, key) DO UPDATE SET value = EXCLUDED.value, created_at = now()",
            tg_id, key[:60], value[:300])


async def save_why(tg_id: int, why: str) -> None:
    p = await pool()
    async with p.acquire() as c:
        await c.execute("UPDATE users SET last_why = $2 WHERE tg_id = $1", tg_id, why[:1000])


async def reset(tg_id: int) -> None:
    p = await pool()
    async with p.acquire() as c:
        await c.execute("DELETE FROM facts WHERE tg_id = $1", tg_id)
        await c.execute("DELETE FROM messages WHERE tg_id = $1", tg_id)
        await c.execute("DELETE FROM users WHERE tg_id = $1", tg_id)


def profile_filled(prof: dict) -> bool:
    return bool(prof) and all(prof.get(f) for f in ("goal", "level", "freq"))
