import asyncio
import os
import shutil
import uuid
import sys
import json
import time
import logging

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
from telegram_checker.backend.tdlib_binding.core import TDLibClient

# إعداد الـ Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("ValidationTool")

ENABLE_RAW_JSON_LOGS = True
API_ID = os.getenv("API_ID", 1234567)  
API_HASH = os.getenv("API_HASH", "dummy_hash")  
LIB_PATH = os.getenv("LIB_PATH", "libtdjson.so")

class ValidationTimer:
    def __init__(self, name):
        self.name = name
        self.start_time = 0
    def __enter__(self):
        self.start_time = time.perf_counter()
        return self
    def __exit__(self, *args):
        elapsed = time.perf_counter() - self.start_time
        logger.info(f"⏱️ [Timing] {self.name}: {elapsed:.4f} seconds")

async def run_single_validation(phone_number: str, index: int):
    session_dir = f"temp_validation_session_{uuid.uuid4().hex[:8]}"
    logger.info(f"\n[{index}] ==============================================")
    logger.info(f"[{index}] بدء التحقق للرقم: {phone_number} | مسار: {session_dir}")

    client = None
    auth_state = "UNKNOWN"
    
    try:
        with ValidationTimer(f"[{index}] Client Initialization"):
            client = TDLibClient(lib_path=LIB_PATH)
        
        def on_update(data):
            nonlocal auth_state
            if ENABLE_RAW_JSON_LOGS:
                logger.debug(f"[{index}] [TDLib Update]: {json.dumps(data, ensure_ascii=False)}")
                
            if data.get("@type") == "updateAuthorizationState":
                auth_state = data["authorization_state"]["@type"]
                logger.info(f"[{index}] [State Change] ➔ {auth_state}")

        client.start(update_handler=on_update)

        with ValidationTimer(f"[{index}] setTdlibParameters"):
            res = await client.send({
                "@type": "setTdlibParameters",
                "use_test_dc": False,
                "database_directory": session_dir,
                "files_directory": session_dir,
                "database_encryption_key": b"".hex(),
                "use_file_database": True, 
                "use_chat_info_database": False,
                "use_message_database": False,
                "use_secret_chats": False,
                "api_id": int(API_ID),
                "api_hash": API_HASH,
                "system_language_code": "en",
                "device_model": "Validation Tool",
                "application_version": "1.0",
            })
            if ENABLE_RAW_JSON_LOGS:
                logger.info(f"[{index}] [Response setTdlibParameters]: {json.dumps(res, ensure_ascii=False)}")

        # Wait for WaitPhoneNumber state
        await asyncio.sleep(1.0)
        
        with ValidationTimer(f"[{index}] setAuthenticationPhoneNumber"):
            logger.info(f"[{index}] Sending setAuthenticationPhoneNumber (Current State: {auth_state})")
            res = await client.send({
                "@type": "setAuthenticationPhoneNumber",
                "phone_number": phone_number,
                "settings": {
                    "@type": "phoneNumberAuthenticationSettings",
                    "allow_flash_call": False,
                    "is_current_phone_number": False,
                    "allow_sms_retriever_api": False
                }
            })
            
            logger.info(f"[{index}] [Actual Final Response]:")
            logger.info(json.dumps(res, indent=2, ensure_ascii=False))
            
            if res.get("@type") == "error":
                logger.error(f"[{index}] Error Code: {res.get('code')}")
                logger.error(f"[{index}] Error Message: {res.get('message')}")
                logger.error(f"[{index}] Auth State at Error: {auth_state}")

    except Exception as e:
        logger.error(f"[{index}] Exception: {e}")
    finally:
        with ValidationTimer(f"[{index}] Client Destruction & Cleanup"):
            if client:
                client.stop()
            if os.path.exists(session_dir):
                shutil.rmtree(session_dir)

async def test_sequential(phones):
    logger.info("=== بدء الاختبار المتتابع (Sequential) ===")
    for i, phone in enumerate(phones):
        await run_single_validation(phone, i)

async def test_parallel(phones):
    logger.info("=== بدء الاختبار المتوازي (Parallel) ===")
    tasks = []
    for i, phone in enumerate(phones):
        tasks.append(run_single_validation(phone, i))
    await asyncio.gather(*tasks)

async def main():
    phones = ["+1234567890", "+999662777"]
    await test_sequential(phones)
    await test_parallel(phones)

if __name__ == "__main__":
    asyncio.run(main())
