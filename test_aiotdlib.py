import asyncio
import sys
from aiotdlib import Client, ClientSettings

async def main():
    try:
        client = Client(
            settings=ClientSettings(
                api_id=2040,
                api_hash="b18441a1ff607e10a989891a5462e627",
                phone_number="+967773335169",
                database_encryption_key="secret",
                files_directory="test_session"
            )
        )
        print("Connecting...")
        await client.connect()
        me = await client.api.get_me()
        print(f"TDLIB_SUCCESS: {me.id}")
    except Exception as e:
        print(f"TDLIB_ERROR: {e}")

if __name__ == '__main__':
    asyncio.run(main())
