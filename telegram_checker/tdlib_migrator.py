import asyncio
import logging
import re
from telethon import TelegramClient
from telethon.sessions import StringSession
import database as db

logger = logging.getLogger(__name__)

# To prevent blocking the main loop with aiotdlib's input(),
# we can just run aiotdlib in a subprocess, and use Telethon to fetch the code.

async def fetch_code_from_telegram(api_id, api_hash, string_session):
    client = TelegramClient(StringSession(string_session), int(api_id), api_hash)
    await client.connect()
    
    # Check if we are authorized
    if not await client.is_user_authorized():
        logger.error("Telethon session is no longer authorized.")
        await client.disconnect()
        return None

    logger.info("Telethon connected. Waiting for login code message...")
    
    # We will look at the last 3 messages from 777000, or wait for a new one.
    # Wait up to 30 seconds for the code.
    for _ in range(30):
        messages = await client.get_messages(777000, limit=2)
        for msg in messages:
            if msg.message and "Login code:" in msg.message or "رمز الدخول:" in msg.message or "code:" in msg.message.lower() or "كود" in msg.message:
                # Extract 5 digit code
                match = re.search(r'\b(\d{5})\b', msg.message)
                if match:
                    # Check age of message to ensure it's fresh (less than 2 mins old)
                    from datetime import datetime, timezone
                    age = (datetime.now(timezone.utc) - msg.date).total_seconds()
                    if age < 120:
                        code = match.group(1)
                        logger.info(f"Successfully extracted Telegram code: {code}")
                        await client.disconnect()
                        return code
        await asyncio.sleep(2)
        
    logger.error("Timeout waiting for Telegram code.")
    await client.disconnect()
    return None

async def migrate_account_to_tdlib(phone, api_id, api_hash, string_session):
    logger.info(f"Starting TDLib migration for {phone}")
    
    # We create a subprocess that runs a simple aiotdlib login script.
    # We will feed it the code when it asks for it.
    
    import tempfile
    import os
    
    script = f"""
import asyncio
import sys
from aiotdlib import Client, ClientSettings

async def main():
    try:
        client = Client(
            settings=ClientSettings(
                api_id={api_id},
                api_hash="{api_hash}",
                phone_number="{phone}",
                database_encryption_key="secret",
                files_directory="sessions/{phone}"
            )
        )
        await client.connect()
        me = await client.api.get_me()
        print(f"TDLIB_SUCCESS: {{me.id}}")
    except Exception as e:
        print(f"TDLIB_ERROR: {{e}}")

if __name__ == '__main__':
    asyncio.run(main())
"""
    script_path = f"/tmp/tdlib_login_{phone.strip('+')}.py"
    with open(script_path, "w") as f:
        f.write(script)
        
    import sys
    process = await asyncio.create_subprocess_exec(
        sys.executable, script_path,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    
    # While aiotdlib is starting, it will trigger the code to be sent.
    # We use Telethon to fetch it.
    code = await fetch_code_from_telegram(api_id, api_hash, string_session)
    
    if not code:
        logger.error(f"Failed to fetch code for {phone}. Killing TDLib process.")
        try:
            process.kill()
        except:
            pass
        return False
        
    # Send code to aiotdlib stdin
    logger.info(f"Sending code {code} to aiotdlib stdin...")
    process.stdin.write(f"{code}\n".encode())
    await process.stdin.drain()
    
    # Wait for completion
    stdout, stderr = await process.communicate()
    out = stdout.decode()
    err = stderr.decode()
    
    if "TDLIB_SUCCESS" in out:
        logger.info(f"Successfully migrated {phone} to TDLib!")
        return True
    else:
        logger.error(f"Failed to migrate {phone} to TDLib. Out: {out}, Err: {err}")
        return False

async def auto_migrate_all():
    accounts = await db.get_telegram_accounts()
    for acc in accounts:
        acc_id, phone, api_id, api_hash, string_session, is_active, flood, total, last_used = acc
        if not is_active:
            continue
            
        import os
        if os.path.exists(f"sessions/{phone}"):
            logger.info(f"TDLib session already exists for {phone}. Skipping migration.")
            continue
            
        await migrate_account_to_tdlib(phone, api_id, api_hash, string_session)
        await asyncio.sleep(5)
