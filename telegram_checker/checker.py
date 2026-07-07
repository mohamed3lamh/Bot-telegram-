import asyncio
import os
import logging
import time
import traceback
import datetime
from telethon import functions, types, TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    FloodWaitError, UserPrivacyRestrictedError, PhoneNumberBannedError,
    SessionPasswordNeededError, PhoneNumberInvalidError,
    PhoneNumberUnoccupiedError, PhoneMigrateError
)
from telethon.tl.types.auth import (
    SentCodeTypeApp, SentCodeTypeSms, SentCodeTypeFlashCall, SentCodeTypeMissedCall, SentCodeTypeEmailCode
)
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
        logger.info("Running Silent Strategy\nImportContacts Request")
        is_registered = False
        user_id_to_delete = None
        try:
            contact = types.InputPhoneContact(client_id=0, phone=phone, first_name="TempCheck", last_name="")
            import_res = await asyncio.wait_for(
                client(functions.contacts.ImportContactsRequest(contacts=[contact])),
                timeout=10.0
            )
            # Print raw response object
            logger.info(f"Telegram Raw Response:\n{repr(import_res)}")

            users_count = len(import_res.users) if import_res.users else 0
            logger.info(f"Imported Users Count: {users_count}")

            if import_res.users:
                is_registered = True
                user_id_to_delete = import_res.users[0].id
            
            # Clean up the contact list to prevent bloating the checker account
            logger.info("DeleteContacts Request")
            if import_res.imported:
                imported_user_id = import_res.imported[0].user_id
                await client(functions.contacts.DeleteContactsRequest(id=[imported_user_id]))
                logger.info("DeleteContacts Success")
            elif user_id_to_delete:
                await client(functions.contacts.DeleteContactsRequest(id=[user_id_to_delete]))
                logger.info("DeleteContacts Success")
            else:
                logger.info("No contacts needed deletion.")
        except Exception as e:
            error_message = str(e)
            exception_class_name = e.__class__.__name__
            logger.warning(
                f"[SilentStrategy] Contact import failed with Exception:\n"
                f"Class: {exception_class_name}\n"
                f"Message: {error_message}\n"
                f"Traceback:\n{traceback.format_exc()}"
            )
            
            # If the checker account itself is banned/deactivated/revoked
            is_checker_dead = (
                exception_class_name in ["AuthKeyUnregisteredError", "UserDeactivatedError", "SessionRevokedError"]
                or "BANNED" in error_message.upper()
                or "AUTH_KEY_UNREGISTERED" in error_message.upper()
                or "DEACTIVATED" in error_message.upper()
                or "REVOKED" in error_message.upper()
            )
            if is_checker_dead:
                await account_manager.disable_account(account["id"])
                try:
                    await telegram_client_manager.disconnect_client(account["id"])
                except Exception:
                    pass
                logger.info(
                    f"\nFINAL RESULT\n"
                    f"ACCOUNT_DISABLED\n"
                    f"Reason: Checker account is banned/unauthorized during SilentStrategy import."
                )
                return {
                    "status": "ACCOUNT_DISABLED",
                    "phone": phone,
                    "status_text": "❌ حساب الفاحص تالف وتم تعطيله"
                }
            elif "FLOOD" in error_message.upper() or "FLOOD_WAIT" in error_message.upper():
                await flood_manager.set_flood(account["id"], 60)
                logger.info(
                    f"\nFINAL RESULT\n"
                    f"FLOOD_WAIT\n"
                    f"Reason: SilentStrategy import hit FloodWait."
                )
                return {
                    "status": "FLOOD_WAIT",
                    "phone": phone,
                    "status_text": "🚫 حظر مؤقت لجهة الاتصال (60 ثانية)"
                }

            logger.info(
                f"\nFINAL RESULT\n"
                f"ERROR\n"
                f"Reason: SilentStrategy import failed with error: {error_message}"
            )
            return {
                "status": "ERROR",
                "phone": phone,
                "status_text": f"⚙️ خطأ أثناء الاستيراد الصامت: {error_message}"
            }

        if is_registered:
            logger.info(
                f"\nFINAL RESULT\n"
                f"HAS_SESSION\n"
                f"Reason: ImportContacts returned user payload for {phone}"
            )
            return {
                "status": "HAS_SESSION",
                "phone": phone,
                "status_text": "⚠️ مسجل"
            }
        else:
            logger.info(
                f"\nFINAL RESULT\n"
                f"UNKNOWN\n"
                f"Reason: ImportContacts returned 0 users for {phone}"
            )
            return {
                "status": "UNKNOWN",
                "phone": phone,
                "status_text": "🔴 غير معروف / معلق"
            }


class AccurateStrategy(BaseCheckStrategy):
    """
    Strategy 3: Accurate Mode
    Directly calls send_code_request via a guest client and determines status.
    - Succeeded with App Type -> HAS_SESSION (⚠️ مسجل)
    - Succeeded with SMS Type -> NO_SESSION (🆕 غير مسجل)
    - Password Needed -> HAS_SESSION (⚠️ مسجل)
    - Unoccupied -> NO_SESSION (🆕 غير مسجل)
    - Banned -> BANNED (📵 محظور)
    - Invalid -> INVALID (⚠️ غير صالح)
    """
    async def check(self, client, phone, account):
        # Initialize a temporary anonymous guest client
        api_id = account["api_id"]
        api_hash = account["api_hash"]
        
        logger.info(f"AccurateStrategy: Creating guest client with API_ID {api_id}...")
        guest_client = TelegramClient(StringSession(), api_id, api_hash)
        await guest_client.connect()
        
        try:
            return await self._check_with_guest(guest_client, phone, account)
        finally:
            try:
                await guest_client.disconnect()
            except Exception:
                pass

    async def _check_with_guest(self, guest_client, phone, account, retry_count=0):
        if retry_count > 3:
            return {
                "status": "ERROR",
                "phone": phone,
                "status_text": "⚙️ خطأ: تجاوز الحد الأقصى لإعادة توجيه DC"
            }
            
        logger.info(f"AccurateStrategy: Calling auth.sendCode via guest client (retry={retry_count})...")
        try:
            t_send_code_start = time.perf_counter()
            sent_code = await guest_client.send_code_request(phone)
            t_send_code_end = time.perf_counter()

            logger.info(f"Telegram Raw Response:\n{repr(sent_code)}")

            sent_code_type_name = sent_code.type.__class__.__name__
            is_app = isinstance(sent_code.type, SentCodeTypeApp)
            
            if is_app:
                logger.info(
                    f"\nFINAL RESULT\n"
                    f"HAS_SESSION\n"
                    f"Reason: Telegram returned SentCodeTypeApp"
                )
                return {
                    "status": "HAS_SESSION",
                    "phone": phone,
                    "status_text": "⚠️ مسجل"
                }
            else:
                logger.info(
                    f"\nFINAL RESULT\n"
                    f"NO_SESSION\n"
                    f"Reason: Telegram returned {sent_code_type_name} (unregistered)"
                )
                return {
                    "status": "NO_SESSION",
                    "phone": phone,
                    "status_text": "🆕 غير مسجل"
                }

        except SessionPasswordNeededError as e:
            logger.info(
                f"Telegram Exception caught: SessionPasswordNeededError (Registered)\n"
                f"\nFINAL RESULT\n"
                f"HAS_SESSION\n"
            )
            return {
                "status": "HAS_SESSION",
                "phone": phone,
                "status_text": "⚠️ مسجل"
            }

        except PhoneNumberUnoccupiedError as e:
            logger.info(
                f"Telegram Exception caught: PhoneNumberUnoccupiedError (Unregistered)\n"
                f"\nFINAL RESULT\n"
                f"NO_SESSION\n"
            )
            return {
                "status": "NO_SESSION",
                "phone": phone,
                "status_text": "🆕 غير مسجل"
            }

        except PhoneNumberBannedError as e:
            logger.info(
                f"Telegram Exception caught: PhoneNumberBannedError (Banned)\n"
                f"\nFINAL RESULT\n"
                f"BANNED\n"
            )
            return {
                "status": "BANNED",
                "phone": phone,
                "status_text": "📵 محظور"
            }

        except PhoneNumberInvalidError as e:
            logger.info(
                f"Telegram Exception caught: PhoneNumberInvalidError (Invalid)\n"
                f"\nFINAL RESULT\n"
                f"INVALID\n"
            )
            return {
                "status": "INVALID",
                "phone": phone,
                "status_text": "⚠️ غير صالح"
            }

        except PhoneMigrateError as e:
            logger.warning(
                f"PhoneMigrateError: DC migration requested to DC {e.new_dc} for {phone}. Re-routing..."
            )
            try:
                await guest_client._switch_dc(e.new_dc)
                await asyncio.sleep(0.5)
                return await self._check_with_guest(guest_client, phone, account, retry_count + 1)
            except Exception as migrate_error:
                logger.error(f"PhoneMigrateError retry failed: {migrate_error}")
                return {
                    "status": "ERROR",
                    "phone": phone,
                    "status_text": f"❌ فشل الاتصال بـ DC {e.new_dc}"
                }

        except FloodWaitError as e:
            logger.info(
                f"Telegram Exception caught: FloodWaitError ({e.seconds}s)\n"
                f"\nFINAL RESULT\n"
                f"FLOOD_WAIT\n"
            )
            return {
                "status": "FLOOD_WAIT",
                "seconds": e.seconds,
                "phone": phone,
                "status_text": f"🚫 حظر مؤقت {e.seconds} ثانية"
            }

        except Exception as e:
            error_message = str(e)
            exception_class_name = e.__class__.__name__
            
            # Check for RECAPTCHA_CHECK signals (which indicates unregistered/signup flow)
            if "RECAPTCHA" in error_message.upper():
                logger.info(
                    f"Telegram Exception caught: {exception_class_name} with RECAPTCHA (Unregistered)\n"
                    f"\nFINAL RESULT\n"
                    f"NO_SESSION\n"
                )
                return {
                    "status": "NO_SESSION",
                    "phone": phone,
                    "status_text": "🆕 غير مسجل"
                }
                
            logger.info(
                f"Telegram Exception caught:\n"
                f"Class: {exception_class_name}\n"
                f"Message: {error_message}\n"
                f"Traceback:\n{traceback.format_exc()}\n"
                f"\nFINAL RESULT\n"
                f"ERROR\n"
            )
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
            logger.info(
                f"Silent Result = {res['status']}\n\n"
                f"Escalating to Accurate Strategy..."
            )
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
        t_start = time.perf_counter()

        logger.info(
            f"\n[STEP 4]\n"
            f"Selected checker account\n"
            f"Account ID: {account.get('id')}\n"
            f"Phone: {account.get('phone', 'N/A')}\n"
            f"API_ID: {account.get('api_id')}\n"
            f"Active: {account.get('is_active')}\n"
            f"Flood Until: {account.get('flood_until')}\n"
            f"\n"
            f"[STEP 5]\n"
            f"Connecting Telethon..."
        )

        try:
            client = await telegram_client_manager.get_client(account)
            logger.info("Connected Successfully\nAuthorized=True")
        except SessionUnauthorizedError:
            await account_manager.disable_account(account["id"])
            logger.warning("Connection Failed: Session unauthorized.")
            t_end = time.perf_counter()
            logger.info(
                f"\n==============================\n"
                f"END OF PHONE\n"
                f"Execution Time: {t_end - t_start:.4f}s\n"
                f"==============================\n"
            )
            return {
                "status": "ACCOUNT_DISABLED",
                "phone": phone,
                "status_text": "❌ حساب الفاحص تالف وتم تعطيله"
            }
        except Exception as e:
            logger.error(f"Connection Failed: {e}")
            t_end = time.perf_counter()
            logger.info(
                f"\n==============================\n"
                f"END OF PHONE\n"
                f"Execution Time: {t_end - t_start:.4f}s\n"
                f"==============================\n"
            )
            return {
                "status": "ERROR",
                "phone": phone,
                "status_text": f"❌ فشل الاتصال بالفاحص: {e}"
            }

        res = await strategy.check(client, phone, account)

        t_end = time.perf_counter()
        logger.info(
            f"\n==============================\n"
            f"END OF PHONE\n"
            f"Execution Time: {t_end - t_start:.4f}s\n"
            f"==============================\n"
        )
        return res


# =====================================================================
# Legacy Adapter for Backward Compatibility
# =====================================================================

class TelegramChecker:
    def __init__(self):
        self.engine = TelegramCheckEngine()

    async def _auto_recovery_loop(self):
        """
        حلقة خلفية تعمل كل 5 دقائق لمحاولة استعادة الحسابات المعطلة
        وإعادتها للخدمة تلقائياً في حال عادت للعمل.
        """
        await asyncio.sleep(30)  # الانتظار قليلاً عند بدء التشغيل لعدم التضارب
        while True:
            try:
                disabled_accounts = await account_manager.get_all_disabled_accounts()
                if disabled_accounts:
                    logger.info(f"[Auto-Recovery] Found {len(disabled_accounts)} disabled accounts. Testing recovery...")
                    for acc in disabled_accounts:
                        try:
                            # محاولة تهيئة الاتصال بدون إثارة أخطاء
                            client = await telegram_client_manager.get_client(acc)
                            if await client.is_user_authorized():
                                logger.info(f"[Auto-Recovery] Account {acc['phone']} is authorized! Recovering...")
                                await account_manager.enable_account(acc["id"])
                        except SessionUnauthorizedError:
                            pass
                        except Exception as e:
                            logger.warning(f"[Auto-Recovery] Failed recovery check for {acc['phone']}: {e}")
            except Exception as e:
                logger.error(f"[Auto-Recovery] Error in recovery loop: {e}")
            
            await asyncio.sleep(300) # فحص كل 5 دقائق

    async def get_available_account(self):
        if not hasattr(self, "_recovery_task_started"):
            self._recovery_task_started = True
            asyncio.create_task(self._auto_recovery_loop())
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
        if not hasattr(self, "_recovery_task_started"):
            self._recovery_task_started = True
            asyncio.create_task(self._auto_recovery_loop())
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
