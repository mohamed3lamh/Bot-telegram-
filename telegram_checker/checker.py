import asyncio
import os
import logging
import time
import traceback
import datetime
from telethon import functions, types
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

class SmartCheckStrategy:
    """
    Unified Smart Checking Strategy:
    1. Phase 1: Silent Contact Import (ImportContactsRequest).
       - If user is successfully imported -> HAS_SESSION (⚠️ مسجل)
    2. Phase 2: Accurate Check (send_code_request) only if Phase 1 returns UNKNOWN.
       - Succeeded with App Type -> HAS_SESSION (⚠️ مسجل)
       - Succeeded with SMS/Flash Type -> NO_SESSION (🆕 غير مسجل)
       - Password Needed -> HAS_SESSION (⚠️ مسجل)
       - Unoccupied -> NO_SESSION (🆕 غير مسجل)
       - Banned -> BANNED (📵 محظور)
       - Invalid -> INVALID (⚠️ غير صالح)
    """
    async def check(self, client, phone, account):
        logger.info("Running Phase 1: Silent Check (ImportContacts)")
        is_registered = False
        user_id_to_delete = None
        
        try:
            contact = types.InputPhoneContact(client_id=0, phone=phone, first_name="TempCheck", last_name="")
            import_res = await asyncio.wait_for(
                client(functions.contacts.ImportContactsRequest(contacts=[contact])),
                timeout=10.0
            )
            logger.info(f"Telegram ImportContacts Response:\n{repr(import_res)}")

            if import_res.users:
                is_registered = True
                user_id_to_delete = import_res.users[0].id
            
            # Clean up the contact list
            if import_res.imported:
                imported_user_id = import_res.imported[0].user_id
                await client(functions.contacts.DeleteContactsRequest(id=[imported_user_id]))
            elif user_id_to_delete:
                await client(functions.contacts.DeleteContactsRequest(id=[user_id_to_delete]))
        except Exception as e:
            error_message = str(e)
            logger.warning(f"[Silent Phase] Contact import failed: {error_message}")
            if "BANNED" in error_message.upper() or "AUTH_KEY_UNREGISTERED" in error_message.upper():
                await account_manager.disable_account(account["id"])
                return {
                    "status": "ACCOUNT_DISABLED",
                    "phone": phone,
                    "status_text": "❌ حساب الفاحص تالف وتم تعطيله"
                }
            elif "FLOOD" in error_message.upper() or "FLOOD_WAIT" in error_message.upper():
                await flood_manager.set_flood(account["id"], 60)
                return {
                    "status": "FLOOD_WAIT",
                    "phone": phone,
                    "status_text": "🚫 حظر مؤقت لجهة الاتصال (60 ثانية)"
                }

        # If user is found during silent phase, return registered status immediately
        if is_registered:
            logger.info("Number found during Silent Check")
            return {
                "status": "HAS_SESSION",
                "phone": phone,
                "status_text": "⚠️ مسجل"
            }

        # Phase 2: Group Invite Trick to distinguish Banned vs Unregistered without sending SMS codes
        logger.info("Running Phase 2: Group Invite Trick")
        
        # We need a small temporary or persistent private channel/group to test invitation.
        # But we can also use contacts.ResolvePhoneRequest or test functions.channels.InviteToChannelRequest.
        # Let's use resolving the phone or inviting to a designated/temporary check channel.
        # A simpler way without requiring group ID beforehand: using contacts.ResolvePhoneRequest first,
        # and if it fails with ContactPhoneUnoccupiedError -> NO_SESSION.
        # If it returns a user, we can verify privacy. If it is banned, it throws PhoneNumberBannedError or is unoccupied.
        # Let's perform a direct invite test to a temporary/designated check channel.
        try:
            # ResolvePhoneRequest: يكشف إذا كان الرقم مسجلاً في تيليجرام بدون إرسال SMS
            try:
                resolved = await client(functions.contacts.ResolvePhoneRequest(phone=phone))
                logger.info(f"Telegram ResolvePhone Response: {repr(resolved)}")
            except Exception as resolve_err:
                # --- تصنيف أي خطأ من ResolvePhoneRequest مباشرةً ---
                err_str = str(resolve_err).upper()
                err_type = type(resolve_err).__name__.upper()

                # أي رسالة تعني "لا يوجد مستخدم بهذا الرقم" → غير مسجل
                NO_SESSION_KEYWORDS = [
                    "NO USER IS ASSOCIATED",
                    "PHONE_NUMBER_UNOCCUPIED",
                    "NO_PHONE_ASSOCIATED",
                    "PHONE_NOT_OCCUPIED",
                    "USER NOT FOUND",
                    "USER_NOT_FOUND",
                    "UNOCCUPIED",
                ]
                if any(kw in err_str or kw in err_type for kw in NO_SESSION_KEYWORDS):
                    logger.info(f"[Phase2] {phone} → NO_SESSION: {resolve_err}")
                    return {
                        "status": "NO_SESSION",
                        "phone": phone,
                        "status_text": "🆕 غير مسجل"
                    }

                # رقم محظور
                if "PHONE_NUMBER_BANNED" in err_str or "PHONENUMBERBANNED" in err_type:
                    return {
                        "status": "BANNED",
                        "phone": phone,
                        "status_text": "📵 محظور"
                    }

                # FloodWait
                if "FLOOD" in err_str or "FLOODWAIT" in err_type:
                    seconds = getattr(resolve_err, "seconds", 60)
                    await flood_manager.set_flood(account["id"], seconds)
                    return {
                        "status": "FLOOD_WAIT",
                        "seconds": seconds,
                        "phone": phone,
                        "status_text": f"🚫 حظر مؤقت {seconds} ثانية"
                    }

                # حساب الفاحص تالف
                if "AUTH_KEY_UNREGISTERED" in err_str or "AUTH_KEY_DUPLICATED" in err_str:
                    await account_manager.disable_account(account["id"])
                    return {
                        "status": "ACCOUNT_DISABLED",
                        "phone": phone,
                        "status_text": "❌ حساب الفاحص تالف وتم تعطيله"
                    }

                # ✅ خصوصية المستخدم: الرقم مسجل لكن يمنع إيجاده
                # UserPrivacyRestrictedError يعني المستخدم موجود لكن له إعدادات خصوصية
                PRIVACY_KEYWORDS = [
                    "PRIVACY",
                    "USERPRIVACYRESTRICTED",
                    "USER_PRIVACY_RESTRICTED",
                    "PRIVACY_RESTRICTED",
                ]
                if any(kw in err_str or kw in err_type for kw in PRIVACY_KEYWORDS):
                    logger.info(f"[Phase2] {phone} → HAS_SESSION (privacy restricted): {resolve_err}")
                    return {
                        "status": "HAS_SESSION",
                        "phone": phone,
                        "status_text": "⚠️ مسجل"
                    }

                # أي خطأ غير معروف آخر → Phase 3: send_code_request كـ fallback موثوق
                logger.warning(f"[Phase2] {phone} → Unknown error, falling back to Phase 3 (send_code_request): {resolve_err}")
                return await self._phase3_send_code(client, phone, account)


            # --- تحليل نتيجة ResolvePhone الناجحة ---
            if resolved.users:
                return {
                    "status": "HAS_SESSION",
                    "phone": phone,
                    "status_text": "⚠️ مسجل"
                }
            else:
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
        except (PhoneNumberUnoccupiedError, PhoneNumberInvalidError):
            return {
                "status": "NO_SESSION",
                "phone": phone,
                "status_text": "🆕 غير مسجل"
            }
        except PhoneMigrateError as e:
            try:
                await telegram_client_manager.disconnect_client(account["id"])
                client2 = await telegram_client_manager.get_client(account)
                await client2._switch_dc(e.new_dc)
                await asyncio.sleep(0.5)
                return await self.check(client2, phone, account)
            except Exception as migrate_error:
                logger.error(f"Migration error DC {e.new_dc}: {migrate_error}")
                return {
                    "status": "ERROR",
                    "phone": phone,
                    "status_text": f"❌ فشل الاتصال بـ DC {e.new_dc}"
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
            error_message = str(e)
            error_upper = error_message.upper()

            NO_SESSION_PATTERNS = [
                "PHONE_NUMBER_UNOCCUPIED",
                "NO USER IS ASSOCIATED",
                "NO_PHONE_ASSOCIATED",
                "PHONE_NOT_OCCUPIED",
                "USER NOT FOUND",
                "USER_NOT_FOUND",
                "UNOCCUPIED",
            ]
            if any(p in error_upper for p in NO_SESSION_PATTERNS):
                logger.info(f"[Phase2] {phone} → NO_SESSION (fallback): {error_message}")
                return {
                    "status": "NO_SESSION",
                    "phone": phone,
                    "status_text": "🆕 غير مسجل"
                }

            if "AUTH_KEY_UNREGISTERED" in error_upper or "AUTH_KEY_DUPLICATED" in error_upper:
                await account_manager.disable_account(account["id"])
                return {
                    "status": "ACCOUNT_DISABLED",
                    "phone": phone,
                    "status_text": "❌ حساب الفاحص تالف وتم تعطيله"
                }

            if "PHONE_NUMBER_BANNED" in error_upper or "USER_BANNED" in error_upper:
                return {
                    "status": "BANNED",
                    "phone": phone,
                    "status_text": "📵 محظور"
                }

            logger.warning(f"[Phase2] {phone} → ERROR (unhandled): {error_message}")
            return {
                "status": "ERROR",
                "phone": phone,
                "status_text": f"⚙️ خطأ من السيرفر: {error_message}"
            }

    async def _phase3_send_code(self, client, phone, account):
        """
        Phase 3: send_code_request - الأكثر دقة 100%.
        - SentCodeTypeApp → مسجل على تيليجرام (HAS_SESSION)
        - SentCodeTypeEmailCode → مسجل (HAS_SESSION)
        - SentCodeTypeSms / Flash / MissedCall → غير مسجل على تيليجرام (NO_SESSION)
        - PhoneNumberUnoccupiedError → غير مسجل (NO_SESSION)
        - PhoneNumberBannedError → محظور (BANNED)
        """
        logger.info(f"[Phase3] Running send_code_request for {phone}")
        try:
            result = await client.send_code_request(phone)
            code_type = type(result.type)
            logger.info(f"[Phase3] {phone} → send_code type: {code_type.__name__}")

            # إلغاء الكود فوراً لتجنب الرسائل الغير مرغوبة
            try:
                await client(functions.auth.CancelCodeRequest(
                    phone_number=phone,
                    phone_code_hash=result.phone_code_hash
                ))
            except Exception:
                pass

            # App أو Email يعني المستخدم مسجل على تيليجرام
            if code_type in (SentCodeTypeApp, SentCodeTypeEmailCode):
                logger.info(f"[Phase3] {phone} → HAS_SESSION (App/Email code type)")
                return {
                    "status": "HAS_SESSION",
                    "phone": phone,
                    "status_text": "⚠️ مسجل"
                }
            else:
                # SMS / Flash / MissedCall = لا يوجد تطبيق تيليجرام = غير مسجل
                logger.info(f"[Phase3] {phone} → NO_SESSION (SMS/Flash code type)")
                return {
                    "status": "NO_SESSION",
                    "phone": phone,
                    "status_text": "🆕 غير مسجل"
                }

        except PhoneNumberUnoccupiedError:
            logger.info(f"[Phase3] {phone} → NO_SESSION (PhoneNumberUnoccupiedError)")
            return {
                "status": "NO_SESSION",
                "phone": phone,
                "status_text": "🆕 غير مسجل"
            }
        except PhoneNumberBannedError:
            logger.info(f"[Phase3] {phone} → BANNED")
            return {
                "status": "BANNED",
                "phone": phone,
                "status_text": "📵 محظور"
            }
        except PhoneNumberInvalidError:
            logger.info(f"[Phase3] {phone} → NO_SESSION (invalid number)")
            return {
                "status": "NO_SESSION",
                "phone": phone,
                "status_text": "🆕 غير مسجل"
            }
        except FloodWaitError as e:
            await flood_manager.set_flood(account["id"], e.seconds)
            logger.warning(f"[Phase3] {phone} → FLOOD_WAIT {e.seconds}s")
            return {
                "status": "FLOOD_WAIT",
                "seconds": e.seconds,
                "phone": phone,
                "status_text": f"🚫 حظر مؤقت {e.seconds} ثانية"
            }
        except SessionPasswordNeededError:
            # حساب الفاحص يحتاج 2FA - هذا لا ينبغي أن يحدث هنا
            logger.warning(f"[Phase3] {phone} → HAS_SESSION (SessionPasswordNeeded on checker?)")
            return {
                "status": "HAS_SESSION",
                "phone": phone,
                "status_text": "⚠️ مسجل"
            }
        except Exception as e:
            err_str = str(e).upper()
            logger.error(f"[Phase3] {phone} → Unexpected error: {e}")
            # آخر محاولة: تصنيف بناءً على الرسالة
            if any(kw in err_str for kw in ["UNOCCUPIED", "NO USER", "NOT FOUND", "NOT_FOUND"]):
                return {"status": "NO_SESSION", "phone": phone, "status_text": "🆕 غير مسجل"}
            if "BANNED" in err_str:
                return {"status": "BANNED", "phone": phone, "status_text": "📵 محظور"}
            if "AUTH_KEY" in err_str:
                await account_manager.disable_account(account["id"])
                return {"status": "ACCOUNT_DISABLED", "phone": phone, "status_text": "❌ حساب الفاحص تالف وتم تعطيله"}
            # في حالة الشك - نعيد ERROR لا NO_SESSION
            return {
                "status": "ERROR",
                "phone": phone,
                "status_text": f"⚙️ خطأ Phase3: {e}"
            }


# =====================================================================
# Main Engine: TelegramCheckEngine
# =====================================================================

class TelegramCheckEngine:
    def __init__(self):
        self.strategy = SmartCheckStrategy()

    async def check_phone(self, account, phone):
        t_start = time.perf_counter()
        logger.info(f"Starting check for {phone} using checker {account.get('id')}")

        try:
            client = await telegram_client_manager.get_client(account)
            logger.info("Connected Successfully")
        except SessionUnauthorizedError:
            await account_manager.disable_account(account["id"])
            t_end = time.perf_counter()
            return {
                "status": "ACCOUNT_DISABLED",
                "phone": phone,
                "status_text": "❌ حساب الفاحص تالف وتم تعطيله"
            }
        except Exception as e:
            logger.error(f"Connection Failed: {e}")
            t_end = time.perf_counter()
            return {
                "status": "ERROR",
                "phone": phone,
                "status_text": f"❌ فشل الاتصال بالفاحص: {e}"
            }

        res = await self.strategy.check(client, phone, account)
        t_end = time.perf_counter()
        logger.info(f"End check for {phone}. Execution time: {t_end - t_start:.4f}s")
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
