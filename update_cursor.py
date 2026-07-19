import re

with open('/root/Bot-telegram-/database.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Make sure we don't duplicate imports if already rewritten
if "class AsyncCursor:" in content:
    # We just need to update AsyncCursor
    new_cursor = """class AsyncCursor:
    def __init__(self, conn):
        self.conn = conn
        self._last_result = None

    async def execute(self, query, params=None):
        query = _convert_query(query)
        try:
            if params:
                self._last_result = await self.conn.fetch(query, *params)
            else:
                self._last_result = await self.conn.fetch(query)
        except asyncpg.exceptions.QueryWithoutReturingError:
            if params:
                await self.conn.execute(query, *params)
            else:
                await self.conn.execute(query)
            self._last_result = []

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
        pass"""
    content = re.sub(r'class AsyncCursor:.*?(?=class AsyncConnectionWrapper:)', new_cursor + '\n\n', content, flags=re.DOTALL)
    with open('/root/Bot-telegram-/database.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print("AsyncCursor updated safely.")
