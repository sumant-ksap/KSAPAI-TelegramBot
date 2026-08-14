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


class Memory:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    @classmethod
    async def connect(cls, dsn: str) -> "Memory":
        pool = await asyncpg.create_pool(dsn)
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
