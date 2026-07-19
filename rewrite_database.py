import re

with open('/root/Bot-telegram-/database.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Replace "def " with "async def "
content = re.sub(r'^def ', r'async def ', content, flags=re.MULTILINE)

# Replace "db_execute(" with "await db_execute("
content = re.sub(r'(?<!await\s)db_execute\(', r'await db_execute(', content)

header = """import os
import time
import logging
import asyncio
import asyncpg
from urllib.parse import urlparse

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")

_pool = None

async def init_pool():
    global _pool
    if not _pool:
        _pool = await asyncpg.create_pool(DATABASE_URL, min_size=5, max_size=35)
        logger.info("✅ Asyncpg Pool Ready")

def _convert_query(query):
    parts = query.split('%s')
    if len(parts) == 1:
        return query
    new_query = parts[0]
    for i in range(1, len(parts)):
        new_query += f"${i}" + parts[i]
    return new_query

class AsyncCursor:
    def __init__(self, conn):
        self.conn = conn
        self._last_result = None

    async def execute(self, query, params=None):
        query = _convert_query(query)
        if params:
            self._last_result = await self.conn.fetch(query, *params)
        else:
            self._last_result = await self.conn.fetch(query)

    async def fetchone(self):
        if self._last_result:
            row = self._last_result.pop(0)
            return tuple(row.values())
        return None

    async def fetchall(self):
        res = [tuple(row.values()) for row in self._last_result] if self._last_result else []
        self._last_result = []
        return res

    async def close(self):
        pass

class AsyncConnectionWrapper:
    def __init__(self, pool):
        self.pool = pool
        self.conn = None

    async def __aenter__(self):
        if not self.pool:
            await init_pool()
        self.conn = await self.pool.acquire()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.pool.release(self.conn)

    def cursor(self):
        return AsyncCursor(self.conn)

    async def commit(self):
        pass

def get_connection():
    return AsyncConnectionWrapper(_pool)

async def db_execute(query, params=None, commit=True, fetch=None):
    async with get_connection() as conn:
        cursor = conn.cursor()
        await cursor.execute(query, params)
        if fetch == "one":
            return await cursor.fetchone()
        elif fetch == "all":
            return await cursor.fetchall()
        return None
"""

init_db_idx = content.find("async def init_db():")
new_content = header + "\n" + content[init_db_idx:]

# Ensure init_pool is called at the beginning
new_content = new_content.replace("async def init_db():\n", "async def init_db():\n    await init_pool()\n")

with open('/root/Bot-telegram-/database.py', 'w', encoding='utf-8') as f:
    f.write(new_content)

print("database.py perfectly rewritten for asyncpg.")
