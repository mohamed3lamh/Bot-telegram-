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
    نظام الفحص الرباعي الهجين الفائق الدقة (Smart Quad-Layer Checker):
    1. الطبقة الأولى: الاستيراد الصامت (ImportContactsRequest) - فحص سريع وصامت.
    2. الطبقة الثانية: فحص الخادم المباشر وتحديد الخصوصية (ResolvePhoneRequest) - لمعالجة قيود الخصوصية والتفريق الدقيق.
    3. الطبقة الثالثة: فحص التدفق بالكود التجريبي (send_code_request) - الملاذ الأخير الحاسم لتحديد وجود التطبيق (App vs SMS) مع إلغاء الكود فوراً وبشكل حاسم لتجنب إرسال أي رسالة للمستهدف.
    """
    async def check(self, client, phone, account):
        # --- الطبقة الأولى: الاستيراد الصامت (Silent Import) ---
        logger.info(f"[Layer 1: Import] Silent Contact Import check for {phone}")
        try:
            contact = types.InputPhoneContact(client_id=0, phone=phone, first_name="TempCheck", last_name="")
            import_res = await asyncio.wait_for(
                client(functions.contacts.ImportContactsRequest(contacts=[contact])),
                timeout=8.0
            )
            
            # تنظيف قائمة جهات الاتصال فوراً
            if import_res.users:
                user_id = import_res.users[0].id
                await client(functions.contacts.DeleteContactsRequest(id=[user_id]))
                logger.info(f"[Layer 1] User found directly! Registered. (Phone: {phone})")
                return {
                    "status": "HAS_SESSION",
                    "phone": phone,
                    "status_text": "⚠️ مسجل"
                }
            
            if import_res.imported:
                imported_user_id = import_res.imported[0].user_id
                await client(functions.contacts.DeleteContactsRequest(id=[imported_user_id]))
                logger.info(f"[Layer 1] Contact imported! Registered. (Phone: {phone})")
                return {
                    "status": "HAS_SESSION",
                    "phone": phone,
                    "status_text": "⚠️ مسجل"
                }

        except PhoneMigrateError as e:
            # معالجة فورية لانتقال مركز البيانات
            logger.info(f"[Layer 1] Phone migrate detected to DC {e.new_dc}. Re-routing...")
            try:
                await telegram_client_manager.disconnect_client(account["id"])
                client2 = await telegram_client_manager.get_client(account)
                await client2._switch_dc(e.new_dc)
                await asyncio.sleep(0.5)
                return await self.check(client2, phone, account)
            except Exception as migrate_error:
                logger.error(f"Migration error: {migrate_error}")
                return {"status": "ERROR", "phone": phone, "status_text": f"❌ فشل الانتقال لـ DC {e.new_dc}"}

        except FloodWaitError as e:
            await flood_manager.set_flood(account["id"], e.seconds)
            return {
                "status": "FLOOD_WAIT",
                "seconds": e.seconds,
                "phone": phone,
                "status_text": f"🚫 حظر مؤقت {e.seconds} ثانية"
            }
        except Exception as e:
            error_message = str(e).upper()
            logger.warning(f"[Layer 1] Silent Phase error: {e}")
            if "BANNED" in error_message or "AUTH_KEY_UNREGISTERED" in error_message:
                await account_manager.disable_account(account["id"])
                return {"status": "ACCOUNT_DISABLED", "phone": phone, "status_text": "❌ حساب الفاحص تالف وتم تعطيله"}

        # --- الطبقة الثانية: فحص الخصوصية والتحقق المباشر (ResolvePhone) ---
        logger.info(f"[Layer 2: ResolvePhone] Running ResolvePhoneRequest for {phone}")
        try:
            resolved = await client(functions.contacts.ResolvePhoneRequest(phone=phone))
            if resolved.users:
                logger.info(f"[Layer 2] User resolved successfully! Registered. (Phone: {phone})")
                return {
                    "status": "HAS_SESSION",
                    "phone": phone,
                    "status_text": "⚠️ مسجل"
                }
            
            # إذا نجح الطلب ولكن لم يرجع مستخدمين، ننتقل للطبقة الثالثة للتحقق المطلق
            logger.info(f"[Layer 2] ResolvePhone returned empty user. Moving to Layer 3...")

        except UserPrivacyRestrictedError:
            # مستخدم مسجل ولكن قام بتشديد إعدادات الخصوصية (دليل قاطع على وجود الحساب!)
            logger.info(f"[Layer 2] Privacy Restricted! Phone is Registered but hidden. (Phone: {phone})")
            return {
                "status": "HAS_SESSION",
                "phone": phone,
                "status_text": "⚠️ مسجل"
            }

        except PhoneNumberUnoccupiedError:
            logger.info(f"[Layer 2] Phone unoccupied. Not registered. (Phone: {phone})")
            return {
                "status": "NO_SESSION",
                "phone": phone,
                "status_text": "🆕 غير مسجل"
            }

        except PhoneNumberBannedError:
            logger.info(f"[Layer 2] Phone is banned. (Phone: {phone})")
            return {
                "status": "BANNED",
                "phone": phone,
                "status_text": "📵 محظور"
            }

        except PhoneNumberInvalidError:
            logger.info(f"[Layer 2] Phone invalid. (Phone: {phone})")
            return {
                "status": "NO_SESSION",
                "phone": phone,
                "status_text": "🆕 غير مسجل"
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
            error_str = str(e).upper()
            error_type = type(e).__name__.upper()
            logger.warning(f"[Layer 2] ResolvePhone error: {e}")

            # فحص الكلمات المفتاحية للخطأ للتعامل الدقيق
            NO_SESSION_KEYWORDS = ["UNOCCUPIED", "NO USER", "NOT FOUND", "NOT_FOUND", "NO_PHONE_ASSOCIATED"]
            BANNED_KEYWORDS = ["BANNED", "PHONE_NUMBER_BANNED"]
            PRIVACY_KEYWORDS = ["PRIVACY", "PRIVACY_RESTRICTED", "USERPRIVACYRESTRICTED"]

            if any(kw in error_str or kw in error_type for kw in PRIVACY_KEYWORDS):
                logger.info(f"[Layer 2] Privacy error detected. Phone is Registered. (Phone: {phone})")
                return {
                    "status": "HAS_SESSION",
                    "phone": phone,
                    "status_text": "⚠️ مسجل"
                }

            if any(kw in error_str or kw in error_type for kw in NO_SESSION_KEYWORDS):
                logger.info(f"[Layer 2] Keyword match: Not registered. (Phone: {phone})")
                return {
                    "status": "NO_SESSION",
                    "phone": phone,
                    "status_text": "🆕 غير مسجل"
                }

            if any(kw in error_str or kw in error_type for kw in BANNED_KEYWORDS):
                logger.info(f"[Layer 2] Keyword match: Banned. (Phone: {phone})")
                return {
                    "status": "BANNED",
                    "phone": phone,
                    "status_text": "📵 محظور"
                }

            if "AUTH_KEY" in error_str:
                await account_manager.disable_account(account["id"])
                return {"status": "ACCOUNT_DISABLED", "phone": phone, "status_text": "❌ حساب الفاحص تالف وتم تعطيله"}

        # --- الطبقة الثالثة: فحص التدفق بالكود التجريبي (send_code_request) ---
        # الملاذ الأخير والحاسم للوصول لدقة 100%
        logger.info(f"[Layer 3: SendCode] Running send_code_request for {phone}")
        try:
            # نرسل طلب توليد كود. تيليجرام سيفحص أولاً إذا كان الرقم له حساب نشط
            result = await client.send_code_request(phone)
            code_type = type(result.type)
            logger.info(f"[Layer 3] Response code type for {phone}: {code_type.__name__}")

            # إلغاء الكود فوراً لمنع إرسال أي SMS أو التسبب بإزعاج للمستخدم
            try:
                await client(functions.auth.CancelCodeRequest(
                    phone_number=phone,
                    phone_code_hash=result.phone_code_hash
                ))
                logger.info(f"[Layer 3] Cancelled verification code for {phone} successfully.")
            except Exception as cancel_err:
                logger.warning(f"[Layer 3] CancelCodeRequest failed (safe to ignore): {cancel_err}")

            # تحليل النتيجة:
            # إذا كان الكود مرسل لـ App أو Email فهذا يعني وجود حساب نشط ومثبت
            if code_type in (SentCodeTypeApp, SentCodeTypeEmailCode):
                logger.info(f"[Layer 3] Code directed to App/Email. Phone is Registered. (Phone: {phone})")
                return {
                    "status": "HAS_SESSION",
                    "phone": phone,
                    "status_text": "⚠️ مسجل"
                }
            else:
                # إذا طلب إرسال SMS أو FlashCall أو MissedCall فهذا يعني أنه لا يوجد جلسة نشطة للمستخدم
                # وبالتالي الحساب غير مسجل حالياً على تيليجرام
                logger.info(f"[Layer 3] Code directed to SMS/Flash. Phone is Not Registered. (Phone: {phone})")
                return {
                    "status": "NO_SESSION",
                    "phone": phone,
                    "status_text": "🆕 غير مسجل"
                }

        except PhoneNumberUnoccupiedError:
            logger.info(f"[Layer 3] Unoccupied error. Phone is Not Registered. (Phone: {phone})")
            return {
                "status": "NO_SESSION",
                "phone": phone,
                "status_text": "🆕 غير مسجل"
            }

        except PhoneNumberBannedError:
            logger.info(f"[Layer 3] Banned error. Phone is Banned. (Phone: {phone})")
            return {
                "status": "BANNED",
                "phone": phone,
                "status_text": "📵 محظور"
            }

        except PhoneNumberInvalidError:
            logger.info(f"[Layer 3] Invalid phone. Phone is Not Registered. (Phone: {phone})")
            return {
                "status": "NO_SESSION",
                "phone": phone,
                "status_text": "🆕 غير مسجل"
            }

        except FloodWaitError as e:
            await flood_manager.set_flood(account["id"], e.seconds)
            logger.warning(f"[Layer 3] FloodWait: {e.seconds} seconds on checker.")
            return {
                "status": "FLOOD_WAIT",
                "seconds": e.seconds,
                "phone": phone,
                "status_text": f"🚫 حظر مؤقت {e.seconds} ثانية"
            }

        except SessionPasswordNeededError:
            # إذا طلب الباسورد، فهذا يعني أن الحساب موجود ومحمي بالتحقق بخطوتين -> مسجل قطعا
            logger.info(f"[Layer 3] Session password needed! Phone is Registered. (Phone: {phone})")
            return {
                "status": "HAS_SESSION",
                "phone": phone,
                "status_text": "⚠️ مسجل"
            }

        except Exception as e:
            error_str = str(e).upper()
            logger.error(f"[Layer 3] Unexpected exception for {phone}: {e}")
            
            if any(kw in error_str for kw in ["UNOCCUPIED", "NO USER", "NOT FOUND", "NOT_FOUND"]):
                return {"status": "NO_SESSION", "phone": phone, "status_text": "🆕 غير مسجل"}
            if "BANNED" in error_str:
                return {"status": "BANNED", "phone": phone, "status_text": "📵 محظور"}
            if "AUTH_KEY" in error_str:
                await account_manager.disable_account(account["id"])
                return {"status": "ACCOUNT_DISABLED", "phone": phone, "status_text": "❌ حساب الفاحص تالف وتم تعطيله"}

            # كخيار أمان أخير لمنع تصنيف الأخطاء العشوائية كغير مسجل
            return {
                "status": "ERROR",
                "phone": phone,
                "status_text": f"⚙️ خطأ نظام: {e}"
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
        if res and res.get("status") not in ["FLOOD_WAIT", "ACCOUNT_DISABLED", "ERROR"]:
            try:
                await flood_manager.account_used(account["id"])
            except Exception as ue:
                logger.error(f"Failed to increment checks count for account {account.get('id')}: {ue}")
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
