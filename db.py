from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse, urlunparse

import asyncpg

SCHEMA = """
CREATE TABLE IF NOT EXISTS ksapai_messages (
    id BIGSERIAL PRIMARY KEY,
    chat_id BIGINT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ksapai_messages_chat_id_created_at_idx ON ksapai_messages (chat_id, created_at);
"""


def _ssl_mode(dsn: str) -> str | None:
    # Render's external hostnames (*.render.com) refuse plaintext, and asyncpg's
    # default "prefer" mode hides the real TLS failure behind a misleading
    # "SSL/TLS required" from its plaintext retry. Internal Render hostnames
    # (dpg-xxxx-a, no dots) and localhost don't need TLS. An explicit sslmode
    # in the DSN always wins.
    parsed = urlparse(dsn)
    if "sslmode" in parse_qs(parsed.query):
        return None
    host = parsed.hostname or ""
    if host in ("localhost", "127.0.0.1") or "." not in host:
        return None
    return "require"


def _strip_unsupported_params(dsn: str) -> str:
    # Neon's connection strings include channel_binding=require, which asyncpg
    # doesn't recognise and would forward to the server as an unknown setting.
    parsed = urlparse(dsn)
    query = [(k, v) for k, v in parse_qsl(parsed.query) if k != "channel_binding"]
    return urlunparse(parsed._replace(query=urlencode(query)))


class Memory:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    @classmethod
    async def connect(cls, dsn: str) -> "Memory":
        dsn = _strip_unsupported_params(dsn)
        pool = await asyncpg.create_pool(dsn, ssl=_ssl_mode(dsn))
        async with pool.acquire() as conn:
            await conn.execute(SCHEMA)
        return cls(pool)

    async def close(self) -> None:
        await self._pool.close()

    async def add_message(self, chat_id: int, role: str, content: str) -> None:
        await self._pool.execute(
            "INSERT INTO ksapai_messages (chat_id, role, content) VALUES ($1, $2, $3)",
            chat_id,
            role,
            content,
        )

    async def history(self, chat_id: int, limit: int) -> list[dict]:
        rows = await self._pool.fetch(
            """
            SELECT role, content FROM (
                SELECT role, content, created_at FROM ksapai_messages
                WHERE chat_id = $1
                ORDER BY created_at DESC
                LIMIT $2
            ) AS recent
            ORDER BY created_at ASC
            """,
            chat_id,
            limit,
        )
        return [{"role": row["role"], "content": row["content"]} for row in rows]

    async def reset(self, chat_id: int) -> None:
        await self._pool.execute("DELETE FROM ksapai_messages WHERE chat_id = $1", chat_id)
