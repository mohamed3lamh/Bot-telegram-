import asyncio
import asyncpg
import sys
import os

async def main():
    try:
        pool = await asyncpg.create_pool(os.getenv("DATABASE_URL") or "postgresql://postgres:postgres@localhost:5432/postgres", min_size=1, max_size=1)
        conn = await pool.acquire()
        try:
            await conn.execute("CREATE TABLE IF NOT EXISTS test (id SERIAL PRIMARY KEY)")
            await conn.fetch("INSERT INTO test DEFAULT VALUES")
        except Exception as e:
            print("EXCEPTION_TYPE:", type(e).__name__)
            print("EXCEPTION_CLASS:", e.__class__.__module__ + "." + e.__class__.__name__)
    except Exception as e:
        print("COULD NOT CONNECT", e)

asyncio.run(main())
