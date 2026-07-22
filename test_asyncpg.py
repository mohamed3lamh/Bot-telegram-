import asyncio
import asyncpg
import sys

async def test():
    conn = await asyncpg.connect() # Needs DB url or use a mock. Wait, no db url available directly without reading env.
    pass

