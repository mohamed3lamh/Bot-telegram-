import re

with open('/root/Bot-telegram-/database.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Add AsyncTTLCache
cache_code = """
import time
import asyncio

class AsyncTTLCache:
    def __init__(self, ttl_seconds=60):
        self.cache = {}
        self.ttl = ttl_seconds
        self.lock = asyncio.Lock()

    async def get(self, key):
        async with self.lock:
            if key in self.cache:
                value, expiry = self.cache[key]
                if time.time() < expiry:
                    return value
                else:
                    del self.cache[key]
            return None

    async def set(self, key, value, ttl=None):
        async with self.lock:
            _ttl = ttl if ttl is not None else self.ttl
            self.cache[key] = (value, time.time() + _ttl)

    async def delete(self, key):
        async with self.lock:
            if key in self.cache:
                del self.cache[key]

    async def clear(self):
        async with self.lock:
            self.cache.clear()

# Cache Instances
settings_cache = AsyncTTLCache(ttl_seconds=600)
user_cache = AsyncTTLCache(ttl_seconds=300)
bot_cache = AsyncTTLCache(ttl_seconds=300)
accounts_cache = AsyncTTLCache(ttl_seconds=300)
countries_cache = AsyncTTLCache(ttl_seconds=300)

"""

if 'class AsyncTTLCache' not in content:
    content = content.replace('DATABASE_URL = os.getenv("DATABASE_URL")', cache_code + '\nDATABASE_URL = os.getenv("DATABASE_URL")')

with open('/root/Bot-telegram-/database.py', 'w', encoding='utf-8') as f:
    f.write(content)
