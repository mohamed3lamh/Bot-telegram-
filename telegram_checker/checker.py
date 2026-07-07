import asyncio
import os
import logging
import time
from telethon import functions, types
from telethon.errors import (
    FloodWaitError, UserPrivacyRestrictedError, PhoneNumberBannedError,
    SessionPasswordNeededError, PhoneNumberInvalidError,
    PhoneNumberUnoccupiedError, PhoneMigrateError
)
from telethon.tl.types.auth import SentCodeTypeApp, SentCodeTypeSms
from .telegram_client import telegram_client_manager, SessionUnauthorizedError
from .account_manager import account_manager
from .flood_manager import flood_manager

logger = logging.getLogger(__name__)

# =====================================================================
# Strategy Pattern: base abstract class and individual strategies
# =====================================================================

class BaseCheckStrategy:
    async def check(self, client, phone, account):
        raise NotImplementedError("Strategies must implement the check method.")


class SilentStrategy(BaseCheckStrategy):
    """
    Strategy 1: Silent Check
    Uses only contacts.importContacts.
    - If user is found -> HAS_SESSION (⚠️ مسجل)
    - If user is not found -> UNKNOWN (🔴 غير معروف / معلق)
    Does NOT call send_code_request, avoiding SMS dispatch.
    """
    async def check(self, client, phone, account):
        is_registered = False
        user_id_to_delete = None
        try:
            contact = types.InputPhoneContact(client_id=0, phone=phone, first_name="TempCheck", last_name="")
            import_res = await asyncio.wait_for(
                client(functions.contacts.ImportContactsRequest(contacts=[contact])),
                timeout=10.0
            )
            if import_res.users:
                is_registered = True
                user_id_to_delete = import_res.users[0].id
            
            # Clean up the contact list to prevent bloating the checker account
            if import_res.imported:
                imported_user_id = import_res.imported[0].user_id
                await client(functions.contacts.DeleteContactsRequest(id=[imported_user_id]))
            elif user_id_to_delete:
                await client(functions.contacts.DeleteContactsRequest(id=[user_id_to_delete]))
        except Exception as e:
            logger.warning(f"[SilentStrategy] Contact import failed for {phone}: {e}")
            error_message = str(e)
            
            # If the checker account itself is banned/deactivated
            if "BANNED" in error_message.upper() or "AUTH_KEY_UNREGISTERED" in error_message.upper():
                await account_manager.disable_account(account["id"])
                return {
                    "status": "ERROR",
                    "phone": phone,
                    "status_text": "❌ حساب الفاحص تالف وتلف"
                }
            elif "FLOOD" in error_message.upper() or "FLOOD_WAIT" in error_message.upper():
                await flood_manager.set_flood(account["id"], 60)
                return {
                    "status": "FLOOD_WAIT",
                    "phone": phone,
                    "status_text": "🚫 حظر مؤقت لجهة الاتصال (60 ثانية)"
                }
            return {
                "status": "ERROR",
                "phone": phone,
                "status_text": f"⚙️ خطأ أثناء الاستيراد الصامت: {error_message}"
            }

        if is_registered:
            return {
                "status": "HAS_SESSION",
                "phone": phone,
                "status_text": "⚠️ مسجل"
            }
        else:
            return {
                "status": "UNKNOWN",
                "phone": phone,
                "status_text": "🔴 غير معروف / معلق"
            }


class AccurateStrategy(BaseCheckStrategy):
    """
    Strategy 3: Accurate Mode
    Directly calls send_code_request and determines status via responses or exceptions.
    - Succeeded with App Type -> HAS_SESSION (⚠️ مسجل)
    - Succeeded with SMS Type -> NO_SESSION (🆕 غير مسجل)
    - Password Needed -> HAS_SESSION (⚠️ مسجل)
    - Unoccupied -> NO_SESSION (🆕 غير مسجل)
    - Banned -> BANNED (📵 محظور)
    - Invalid -> INVALID (⚠️ غير صالح)
    """
    async def check(self, client, phone, account):
        try:
            t_send_code_start = time.perf_counter()
            sent_code = await client.send_code_request(phone)
            t_send_code_end = time.perf_counter()

            logger.info(
                f"[AccurateStrategy] [Checker ID: {account.get('id')}] send_code_request duration: "
                f"{t_send_code_end - t_send_code_start:.4f}s"
            )

            # Check sent_code type to differentiate registered vs unregistered
            if isinstance(sent_code.type, SentCodeTypeApp):
                return {
                    "status": "HAS_SESSION",
                    "phone": phone,
                    "status_text": "⚠️ مسجل"
                }
            else:
                # SentCodeTypeSms means code sent via SMS -> Unregistered
                return {
                    "status": "NO_SESSION",
                    "phone": phone,
                    "status_text": "🆕 غير مسجل"
                }

        except SessionPasswordNeededError:
            return {
                "status": "HAS_SESSION",
                "phone": phone,
                "status_text": "⚠️ مسجل"
            }

        except PhoneNumberUnoccupiedError:
            return {
                "status": "NO_SESSION",
                "phone": phone,
                "status_text": "🆕 غير مسجل"
            }

        except PhoneNumberBannedError:
            return {
                "status": "BANNED",
                "phone": phone,
                "status_text": "📵 محظور"
            }

        except PhoneNumberInvalidError:
            return {
                "status": "INVALID",
                "phone": phone,
                "status_text": "⚠️ غير صالح"
            }

        except PhoneMigrateError as e:
            logger.warning(f"[AccurateStrategy] DC migration requested to DC {e.dc} for {phone}. Re-routing...")
            try:
                await telegram_client_manager.disconnect_client(account["id"])
                client2 = await telegram_client_manager.get_client(account)
                await client2._switch_dc(e.dc)
                await asyncio.sleep(0.5)
                # Retry inside the correct DC
                return await self.check(client2, phone, account)
            except Exception as migrate_error:
                logger.error(f"[AccurateStrategy] DC migration failed: {migrate_error}")
                return {
                    "status": "ERROR",
                    "phone": phone,
                    "status_text": f"❌ فشل الاتصال بـ DC {e.dc}"
                }

        except FloodWaitError as e:
            await flood_manager.set_flood(account["id"], e.seconds)
            return {
                "status": "FLOOD_WAIT",
                "seconds": e.seconds,
                "phone": phone,
                "status_text": f"🚫 حظر مؤقت {e.seconds} ثانية"
            }

        except Exception as e:
            try:
                await telegram_client_manager.disconnect_client(account["id"])
            except Exception:
                pass

            error_message = str(e)
            
            # If the checker account itself is banned/deactivated
            if "BANNED" in error_message.upper() or "AUTH_KEY_UNREGISTERED" in error_message.upper():
                await account_manager.disable_account(account["id"])
                return {
                    "status": "ERROR",
                    "phone": phone,
                    "status_text": "❌ حساب الفاحص تالف وتلف"
                }

            return {
                "status": "ERROR",
                "phone": phone,
                "status_text": f"⚙️ خطأ من السيرفر: {error_message}"
            }


class HybridStrategy(BaseCheckStrategy):
    """
    Strategy 2: Smart Verification (Hybrid)
    Executes SilentStrategy first.
    If the result is UNKNOWN, or if the silent check fails/errors out (due to contact import limits),
    upgrades to AccurateStrategy (send_code_request) to get a definitive answer.
    """
    def __init__(self):
        self.silent = SilentStrategy()
        self.accurate = AccurateStrategy()

    async def check(self, client, phone, account):
        # Phase 1: Silent check (Import contacts)
        res = await self.silent.check(client, phone, account)
        
        # If successfully resolved to HAS_SESSION (Registered), return immediately
        if res["status"] == "HAS_SESSION":
            return res

        # If it returned UNKNOWN, or if it failed (ERROR / FLOOD_WAIT) due to contact import limits,
        # fallback to the Accurate verification (send_code_request) to get a precise answer.
        if res["status"] in ["UNKNOWN", "ERROR", "FLOOD_WAIT"]:
            logger.info(f"[HybridStrategy] Silent check returned {res['status']} for {phone}. Upgrading to Accurate check...")
            return await self.accurate.check(client, phone, account)

        return res

# =====================================================================
# Main Engine: TelegramCheckEngine
# =====================================================================

class TelegramCheckEngine:
    def __init__(self):
        self.strategies = {
            "silent": SilentStrategy(),
            "hybrid": HybridStrategy(),
            "accurate": AccurateStrategy()
        }

    def get_mode(self):
        """
        Fetches the active mode from DB or environment variables.
        Supported modes: "silent", "hybrid", "accurate".
        Default: "hybrid".
        """
        try:
            import database as db
            mode = db.get_setting("checking_mode")
            if mode and mode.lower() in self.strategies:
                return mode.lower()
        except Exception as e:
            logger.warning(f"[Engine] Failed to get checking_mode from DB: {e}")

        env_mode = os.getenv("CHECKING_MODE")
        if env_mode and env_mode.lower() in self.strategies:
            return env_mode.lower()

        return "hybrid"

    async def check_phone(self, account, phone):
        mode = self.get_mode()
        strategy = self.strategies[mode]
        logger.info(f"[Engine] Using strategy: '{strategy.__class__.__name__}' (Mode: '{mode}') to verify {phone}")

        try:
            client = await telegram_client_manager.get_client(account)
        except SessionUnauthorizedError:
            await account_manager.disable_account(account["id"])
            return {
                "status": "ACCOUNT_DISABLED",
                "phone": phone,
                "status_text": "❌ حساب الفاحص تالف وتم تعطيله"
            }
        except Exception as e:
            return {
                "status": "ERROR",
                "phone": phone,
                "status_text": f"❌ فشل الاتصال بالفاحص: {e}"
            }

        return await strategy.check(client, phone, account)


# =====================================================================
# Legacy Adapter for Backward Compatibility
# =====================================================================

class TelegramChecker:
    def __init__(self):
        self.engine = TelegramCheckEngine()

    async def get_available_account(self):
        return await account_manager.get_available_account()

    async def wait_for_account(self):
        """
        Smart Wait: If all checkers are in FloodWait, sleeps precisely 
        until the earliest flooded account becomes free.
        """
        while True:
            account = await self.get_available_account()
            if account:
                return account
            
            sleep_time = await account_manager.get_seconds_until_next_available()
            logger.warning(f"[Checker] All accounts in FloodWait or disabled. Sleeping smartly for {sleep_time:.2f}s...")
            await asyncio.sleep(sleep_time)

    async def check_phone(self, account, phone):
        return await self.engine.check_phone(account, phone)

    async def check_numbers(self, phones, callback=None):
        results = []
        for phone in phones:
            account = await self.wait_for_account()
            result = await self.check_phone(account, phone)
            
            # Retry if the current checker account hits Flood or gets deactivated during the request
            if result["status"] in ["FLOOD_WAIT", "ACCOUNT_DISABLED"]:
                account_retry = await self.wait_for_account()
                result = await self.check_phone(account_retry, phone)
                
            results.append(result)
            if callback:
                await callback(result)
        return results


class BatchChecker:
    def __init__(self, checker):
        self.checker = checker

    async def worker(self, account, queue, callback=None, active_workers=None):
        try:
            while True:
                phone = await queue.get()

                if phone is None:
                    queue.task_done()
                    break

                result = await self.checker.check_phone(account, phone)

                if result["status"] in ["FLOOD_WAIT", "FLOOD"]:
                    seconds = result.get("seconds", 60)
                    await flood_manager.set_flood(account["id"], seconds)
                    queue.put_nowait(phone)
                    queue.task_done()
                    break

                elif result["status"] in ["ACCOUNT_DISABLED", "CHECKER_BANNED"]:
                    queue.put_nowait(phone)
                    queue.task_done()
                    break

                if callback:
                    await callback(result)

                queue.task_done()

        finally:
            if active_workers is not None:
                async with active_workers["lock"]:
                    active_workers["count"] -= 1
                    is_last = active_workers["count"] <= 0

                if is_last:
                    drained = 0
                    while not queue.empty():
                        try:
                            queue.get_nowait()
                            queue.task_done()
                            drained += 1
                        except asyncio.QueueEmpty:
                            break

                    if drained:
                        logger.warning(
                            f"BatchChecker: تم تفريغ {drained} رقم من الطابور بدون فحص."
                        )

    async def run(self, phones, callback=None):
        queue = asyncio.Queue()

        for phone in phones:
            await queue.put(phone)

        accounts = await account_manager.get_all_accounts()

        workers = []
        active_workers = {"count": 0, "lock": asyncio.Lock()}

        for account in accounts:
            if await flood_manager.is_flooded(account["id"]):
                continue

            active_workers["count"] += 1

            task = asyncio.create_task(
                self.worker(account, queue, callback, active_workers)
            )
            workers.append(task)

        if not workers:
            logger.warning("BatchChecker: لا توجد حسابات فاحصة متاحة.")
            return False

        await queue.join()

        for _ in workers:
            await queue.put(None)

        await asyncio.gather(*workers, return_exceptions=True)

        return True


telegram_checker = TelegramChecker()
batch_checker = BatchChecker(telegram_checker)
