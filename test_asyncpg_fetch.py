import asyncio
import asyncpg
import os

DATABASE_URL = os.getenv("DATABASE_URL")

async def test():
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute("CREATE TABLE IF NOT EXISTS test_fetch (id int)")
        try:
            res = await conn.fetch("INSERT INTO test_fetch VALUES (1)")
            print("FETCH SUCCESS:", res)
        except Exception as e:
            print("FETCH ERROR:", type(e).__name__, e)
    finally:
        await conn.close()

asyncio.run(test())
