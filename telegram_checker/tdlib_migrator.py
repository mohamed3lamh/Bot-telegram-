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
            if msg.message:
                logger.info(f"Checking message: {msg.message}")
            if msg.message and ("login code" in msg.message.lower() or "رمز الدخول" in msg.message or "code:" in msg.message.lower() or "كود" in msg.message):
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
from aiotdlib.api import API, UpdateAuthorizationState
from aiotdlib.api.types import (
    AuthorizationStateWaitPhoneNumber,
    AuthorizationStateWaitCode,
    AuthorizationStateReady,
    AuthorizationStateWaitPassword,
    AuthorizationStateClosed
)

async def main():
    client = Client(
        settings=ClientSettings(
            api_id={api_id},
            api_hash="{api_hash}",
            database_encryption_key="secret",
            files_directory="sessions/{phone}"
        )
    )
    
    @client.on_event(UpdateAuthorizationState)
    async def on_auth_state(client: Client, update: UpdateAuthorizationState):
        state = update.authorization_state
        print(f"TDLIB_STATE: {{type(state).__name__}}", flush=True)
        if isinstance(state, AuthorizationStateWaitPhoneNumber):
            await client.api.set_authentication_phone_number(phone_number="{phone}")
        elif isinstance(state, AuthorizationStateWaitCode):
            # Wait for code to be written to a file by the parent process
            code_file = "/tmp/tdlib_code_{phone.strip('+')}.txt"
            print("TDLIB_WAITING_FOR_CODE", flush=True)
            for _ in range(60):
                import os
                if os.path.exists(code_file):
                    with open(code_file, "r") as cf:
                        code = cf.read().strip()
                    if code:
                        await client.api.check_authentication_code(code=code)
                        break
                await asyncio.sleep(1)
        elif isinstance(state, AuthorizationStateReady):
            me = await client.api.get_me()
            print(f"TDLIB_SUCCESS: {{me.id}}", flush=True)
            await client.api.close()
        elif isinstance(state, AuthorizationStateClosed):
            pass

    try:
        await client.__aenter__()
        # Keep running until closed
        while not isinstance(client.authorization_state, AuthorizationStateClosed):
            await asyncio.sleep(1)
    except Exception as e:
        print(f"TDLIB_ERROR: {{e}}", flush=True)
        sys.exit(1)

if __name__ == '__main__':
    asyncio.run(main())
"""
    script_path = f"/tmp/tdlib_login_{phone.strip('+')}.py"
    code_path = f"/tmp/tdlib_code_{phone.strip('+')}.txt"
    if os.path.exists(code_path):
        os.remove(code_path)
    with open(script_path, "w") as f:
        f.write(script)
     
    import asyncio
    
    # Start the subprocess
    import sys
    process = await asyncio.create_subprocess_exec(
        sys.executable, script_path,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    
    async def log_subprocess_output(stream, prefix):
        while True:
            line = await stream.readline()
            if not line:
                break
            logger.info(f"[TDLib Subprocess {prefix}] {line.decode().strip()}")

    # Run log readers in the background
    asyncio.create_task(log_subprocess_output(process.stdout, "OUT"))
    asyncio.create_task(log_subprocess_output(process.stderr, "ERR"))
    
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
        
    # Send code to aiotdlib via file
    logger.info(f"Writing code {code} to {code_path} for aiotdlib...")
    with open(code_path, "w") as f:
        f.write(code)
    
    # Wait for completion
    await process.wait()
    
    if process.returncode == 0:
        logger.info(f"Successfully migrated {phone} to TDLib!")
        return True
    else:
        logger.error(f"Failed to migrate {phone} to TDLib. Process exited with code {process.returncode}")
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
