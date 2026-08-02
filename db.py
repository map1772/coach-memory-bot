"""Хранилище бота: профиль, история диалога, факты, добытые из разговора.

Три таблицы вместо одной, потому что у них разная жизнь: профиль правится
редко и целиком, история пишется на каждое сообщение и читается окном, факты
приходят по одному и должны перезаписываться по ключу.
"""
import asyncio
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
    created_at TIMESTAMPTZ DEFAULT now()
);
-- Ключ был (tg_id, key), и два ограничения по здоровью подряд затирали друг друга:
-- человек называл травму и что ему можно, а до профиля доезжало только последнее.
-- Ограничений и предпочтений по определению бывает несколько, поэтому уникальна
-- теперь пара ключ-значение, а расхождения разруливает правило свежести в промпте.
ALTER TABLE facts DROP CONSTRAINT IF EXISTS facts_pkey;
CREATE UNIQUE INDEX IF NOT EXISTS facts_uniq ON facts (tg_id, key, value);
"""

PROFILE_FIELDS = ("name", "goal", "level", "age", "limits", "equipment", "freq")
_pool: asyncpg.Pool | None = None
_pool_lock = asyncio.Lock()


async def pool() -> asyncpg.Pool:
    """Лок нужен не сегодня, а завтра: сейчас пул греется на старте до приёма
    трафика, но первый же вызов из другого места открыл бы гонку на два пула."""
    global _pool
    if _pool is None:
        async with _pool_lock:
            if _pool is None:
                return await _create()
    return _pool


async def _create() -> asyncpg.Pool:
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
    prof["facts"] = merge_facts(facts)
    return prof


def merge_facts(rows) -> dict[str, str]:
    """У одного ключа бывает несколько значений: ограничений и предпочтений по
    определению несколько. Склеиваем в порядке появления, свежее оказывается
    справа, и правило свежести в промпте продолжает работать."""
    merged: dict[str, list[str]] = {}
    for r in rows:
        vals = merged.setdefault(r["key"], [])
        if r["value"] not in vals:
            vals.append(r["value"])
    return {k: "; ".join(v) for k, v in merged.items()}


async def save_profile(tg_id: int, **fields) -> None:
    """Профиль пишется целиком после анкеты и точечно при правках."""
    fields = {k: v for k, v in fields.items() if k in PROFILE_FIELDS}
    if not fields:
        return                      # иначе соберётся INSERT с пустым списком колонок
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


MAX_FACTS = 40                    # весь список печатается в промпт на каждом запросе


async def save_fact(tg_id: int, key: str, value: str) -> None:
    """Факт из живой реплики.

    Потолок нужен не для порядка, а из-за денег: факты уходят в промпт целиком,
    а дедупликация идёт по паре ключ-значение, поэтому перефразировки копятся и
    каждое следующее сообщение такого собеседника дорожает. Лишнее вытесняем по
    старшинству, свежее сказанное человеком важнее давнего.
    """
    p = await pool()
    async with p.acquire() as c:
        await c.execute(
            "INSERT INTO facts (tg_id, key, value) VALUES ($1, $2, $3) "
            "ON CONFLICT (tg_id, key, value) DO UPDATE SET created_at = now()",
            tg_id, key[:60], value[:300])
        # подзапрос отдаёт NULL, пока фактов меньше потолка, и тогда сравнение
        # ложно для всех строк, то есть не удаляется ничего
        await c.execute(
            "DELETE FROM facts WHERE tg_id = $1 AND created_at < ("
            "  SELECT created_at FROM facts WHERE tg_id = $1"
            "  ORDER BY created_at DESC OFFSET $2 LIMIT 1)",
            tg_id, MAX_FACTS)


async def save_why(tg_id: int, why: str) -> None:
    p = await pool()
    async with p.acquire() as c:
        await c.execute("UPDATE users SET last_why = $2 WHERE tg_id = $1", tg_id, why[:1000])


async def reset(tg_id: int) -> None:
    p = await pool()
    async with p.acquire() as c, c.transaction():
        await c.execute("DELETE FROM facts WHERE tg_id = $1", tg_id)
        await c.execute("DELETE FROM messages WHERE tg_id = $1", tg_id)
        await c.execute("DELETE FROM users WHERE tg_id = $1", tg_id)


def profile_filled(prof: dict) -> bool:
    return bool(prof) and all(prof.get(f) for f in ("goal", "level", "freq"))
