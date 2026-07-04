import asyncio
import logging
from telethon.errors import (
    FloodWaitError, PhoneNumberBannedError,
    SessionPasswordNeededError, PhoneNumberInvalidError,
    PhoneMigrateError, NetworkMigrateError, UserMigrateError,
    PhoneNumberUnoccupiedError
)
from .telegram_client import telegram_client_manager, SessionUnauthorizedError
from .account_manager import account_manager
from .flood_manager import flood_manager

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────
# إعداد إشعارات المشرف (يُعيّن من main_bot عند بدء التشغيل)
# ──────────────────────────────────────────────────────────────────
_admin_notify_callback = None   # async callable(message: str)


def set_admin_notify(callback):
    """
    تعيين دالة الإشعار التي ترسل رسائل للمشرف.
    يُستدعى من main_bot عند تهيئة التطبيق:
        checker.set_admin_notify(lambda msg: bot.send_message(ADMIN_ID, msg))
    """
    global _admin_notify_callback
    _admin_notify_callback = callback


async def _notify_admin(message: str):
    """إرسال إشعار للمشرف إذا كانت الدالة مُعيَّنة."""
    if _admin_notify_callback:
        try:
            await _admin_notify_callback(message)
        except Exception as e:
            logger.warning(f"[Notify] فشل إرسال الإشعار للمشرف: {e}")


class TelegramChecker:
    def __init__(self):
        pass

    async def check_phone(self, account: dict, phone: str) -> dict:
        """
        فحص حالة رقم هاتف عبر Telegram API.

        ⚡ التصميم:
        - يُكتسب قفل الحساب (account lock) قبل أي عملية على الـ client.
        - هذا يضمن أن مهمة واحدة فقط تستخدم نفس جلسة Telethon في أي لحظة.
        - يمنع التعارض (Race Condition) وتلف الاتصال الداخلي لـ Telethon.
        """
        import time
        account_id = account["id"]

        # ──────────────────────────────────────────────────
        # اكتساب القفل الخاص بالحساب قبل أي عملية
        # يضمن عدم وجود عمليتين متزامنتين على نفس الجلسة
        # ──────────────────────────────────────────────────
        lock = telegram_client_manager.get_account_lock(account_id)

        async with lock:
            t_start = time.perf_counter()

            # ── الخطوة 1: الحصول على الـ client المتصل ──
            try:
                client = await telegram_client_manager.get_client(account)
                logger.info(
                    f"[Checker] #{account_id}: get_client أُنجز في "
                    f"{time.perf_counter() - t_start:.3f}s"
                )
            except SessionUnauthorizedError:
                logger.warning(
                    f"[Checker] #{account_id}: الجلسة منتهية — "
                    f"توقف مؤقت 6 ساعات (21600s)"
                )
                await flood_manager.set_flood(account_id, seconds=21600)
                try:
                    await telegram_client_manager.disconnect_client(account_id)
                except Exception:
                    pass
                # ─── إشعار المشرف فور انتهاء الجلسة ───
                await _notify_admin(
                    f"⚠️ *انتهاء جلسة فاحص*\n"
                    f"الحساب رقم `{account_id}` انتهت جلسته.\n"
                    f"يرجى إعادة تسجيل الدخول لاستئناف الفحص."
                )
                return {
                    "status": "SESSION_EXPIRED",
                    "phone": phone,
                    "status_text": "⚠️ جلسة الفاحص منتهية — إعادة محاولة بعد 6 ساعات"
                }
            except Exception as e:
                logger.error(f"[Checker] #{account_id}: فشل get_client: {e}")
                try:
                    await telegram_client_manager.disconnect_client(account_id)
                except Exception:
                    pass
                return {
                    "status": "ERROR",
                    "error": str(e),
                    "phone": phone,
                    "status_text": "⚪️ غير معروف / معلق"
                }

            # ── الخطوة 2: إرسال طلب الكود لمعرفة حالة الرقم ──
            try:
                t_req = time.perf_counter()
                await asyncio.wait_for(
                    client.send_code_request(phone),
                    timeout=15.0
                )
                logger.info(
                    f"[Checker] #{account_id}: send_code_request أُنجز في "
                    f"{time.perf_counter() - t_req:.3f}s"
                )
                # وصلنا هنا بدون استثناء → الرقم مسجل في Telegram لكن بدون جلسة نشطة
                return {
                    "status": "NO_SESSION",
                    "phone": phone,
                    "status_text": "✅ الرقم بدون جلسة"
                }

            except SessionPasswordNeededError:
                return {
                    "status": "HAS_SESSION",
                    "phone": phone,
                    "status_text": "⚠️ الرقم لديه جلسة"
                }
            except PhoneNumberBannedError:
                return {
                    "status": "BANNED",
                    "phone": phone,
                    "status_text": "📵 مـحـظـور"
                }
            except PhoneNumberInvalidError:
                return {
                    "status": "INVALID",
                    "phone": phone,
                    "status_text": "⚠️ رقم غير صالح"
                }
            except PhoneNumberUnoccupiedError:
                # الرقم غير مسجل في تيليغرام → بدون جلسة بالتأكيد
                logger.info(
                    f"[Checker] #{account_id}: الرقم {phone} غير مسجل في تيليغرام (NO_SESSION)"
                )
                return {
                    "status": "NO_SESSION",
                    "phone": phone,
                    "status_text": "✅ الرقم بدون جلسة"
                }
            except (PhoneMigrateError, NetworkMigrateError, UserMigrateError) as e:
                # الرقم مسجل في DC مختلف — نفصل ونحاول مجدداً عبر DC الصحيح
                new_dc = getattr(e, 'new_dc', '?')
                logger.info(
                    f"[Checker] #{account_id}: الرقم {phone} في DC {new_dc} — "
                    f"إعادة الاتصال بالـ DC الصحيح..."
                )
                try:
                    await telegram_client_manager.disconnect_client(account_id)
                except Exception:
                    pass
                try:
                    # إعادة الاتصال — Telethon ستتوجه للـ DC الصحيح تلقائياً
                    client2 = await telegram_client_manager.get_client(account)
                    await asyncio.wait_for(
                        client2.send_code_request(phone),
                        timeout=15.0
                    )
                    logger.info(
                        f"[Checker] #{account_id}: نجح send_code_request بعد DC migration"
                    )
                    return {
                        "status": "NO_SESSION",
                        "phone": phone,
                        "status_text": "✅ الرقم بدون جلسة"
                    }
                except SessionPasswordNeededError:
                    return {
                        "status": "HAS_SESSION",
                        "phone": phone,
                        "status_text": "⚠️ الرقم لديه جلسة"
                    }
                except PhoneNumberUnoccupiedError:
                    return {
                        "status": "NO_SESSION",
                        "phone": phone,
                        "status_text": "✅ الرقم بدون جلسة"
                    }
                except PhoneNumberBannedError:
                    return {
                        "status": "BANNED",
                        "phone": phone,
                        "status_text": "📵 مـحـظـور"
                    }
                except FloodWaitError as fe:
                    await flood_manager.set_flood(account_id, fe.seconds)
                    return {
                        "status": "FLOOD",
                        "seconds": fe.seconds,
                        "phone": phone,
                        "status_text": f"🚫 حظر مؤقت {fe.seconds} ثانية"
                    }
                except Exception as retry_e:
                    logger.error(
                        f"[Checker] #{account_id}: فشل إعادة المحاولة بعد DC migration: {retry_e}"
                    )
                    try:
                        await telegram_client_manager.disconnect_client(account_id)
                    except Exception:
                        pass
                    return {
                        "status": "ERROR",
                        "error": str(retry_e),
                        "phone": phone,
                        "status_text": "⚪️ غير معروف / معلق"
                    }
            except FloodWaitError as e:
                logger.warning(
                    f"[Checker] #{account_id}: FloodWait {e.seconds}s"
                )
                await flood_manager.set_flood(account_id, e.seconds)
                return {
                    "status": "FLOOD",
                    "seconds": e.seconds,
                    "phone": phone,
                    "status_text": f"🚫 حظر مؤقت {e.seconds} ثانية"
                }
            except asyncio.TimeoutError:
                logger.warning(
                    f"[Checker] #{account_id}: انتهت مهلة send_code_request — "
                    f"فصل الاتصال للإعادة لاحقاً"
                )
                try:
                    await telegram_client_manager.disconnect_client(account_id)
                except Exception:
                    pass
                return {
                    "status": "ERROR",
                    "error": "timeout",
                    "phone": phone,
                    "status_text": "⏱️ انتهت المهلة — سيُعاد المحاولة"
                }
            except Exception as e:
                logger.error(
                    f"[Checker] #{account_id}: خطأ غير متوقع: {e}"
                )
                try:
                    await telegram_client_manager.disconnect_client(account_id)
                except Exception:
                    pass
                return {
                    "status": "ERROR",
                    "error": str(e),
                    "phone": phone,
                    "status_text": "⚪️ غير معروف / معلق"
                }

    async def get_available_account(self) -> dict | None:
        """
        يُرجع الحساب الأقل استخداماً (Round Robin بسيط مبني على flood_manager).
        يُفضّل الحسابات غير المحظورة ويعيد أولها متاحاً.
        """
        accounts = await account_manager.get_available_accounts()
        if not accounts:
            return None
        # فلترة الحسابات التي لا تزال في FloodWait
        for acct in accounts:
            if not await flood_manager.is_flooded(acct["id"]):
                return acct
        return None

    async def wait_for_account(self) -> dict:
        """ينتظر حتى يتوفر حساب فاحص."""
        while True:
            account = await self.get_available_account()
            if account:
                return account
            logger.info("[Checker] لا يوجد حساب فاحص متاح، إعادة المحاولة بعد 10 ثوانٍ...")
            await asyncio.sleep(10)

    async def check_numbers(self, phones: list, callback=None) -> list:
        """فحص قائمة أرقام بشكل تسلسلي."""
        results = []
        for phone in phones:
            account = await self.wait_for_account()
            result = await self.check_phone(account, phone)
            if result["status"] in ["FLOOD", "SESSION_EXPIRED", "ACCOUNT_DISABLED"]:
                continue
            results.append(result)
            if callback:
                await callback(result)
        return results


class BatchChecker:
    def __init__(self, checker: TelegramChecker):
        self.checker = checker

    async def worker(self, account: dict, queue: asyncio.Queue, callback=None):
        """
        عامل لحساب Telegram واحد.
        check_phone داخلياً مقيّد بالقفل لذا لا يوجد تزامن على الجلسة.
        """
        while True:
            try:
                phone = await queue.get()
            except asyncio.CancelledError:
                break

            if phone is None:
                queue.task_done()
                break

            result = await self.checker.check_phone(account, phone)

            if result["status"] == "FLOOD":
                await flood_manager.set_flood(account["id"], result["seconds"])
                queue.put_nowait(phone)
                queue.task_done()
                break
            elif result["status"] in ["SESSION_EXPIRED", "ACCOUNT_DISABLED"]:
                queue.put_nowait(phone)
                queue.task_done()
                break

            if callback:
                try:
                    await callback(result)
                except Exception as cb_err:
                    logger.warning(f"[BatchChecker] خطأ في callback: {cb_err}")
            queue.task_done()

    async def run(self, phones: list, callback=None) -> bool:
        """
        تشغيل الفحص المتوازي — حساب واحد = عامل واحد.

        التحسينات:
        - إذا انتهى جميع الـ workers بسبب FLOOD، يُنتظر حتى تنتهي مدة الحظر
          ثم يُعاد إطلاق workers جديدة تلقائياً (Auto-Recovery).
        - يُعيد جلب الحسابات عند كل جولة لاستيعاب أي حسابات جديدة أُضيفت.
        """
        if not phones:
            return True

        queue: asyncio.Queue = asyncio.Queue()
        for phone in phones:
            await queue.put(phone)

        while not queue.empty():
            # ── جلب الحسابات من جديد في كل جولة ──────────────────────────
            # يضمن أن الحسابات المضافة حديثاً تدخل التوزيع فوراً
            all_accounts = await account_manager.get_all_accounts()

            workers = []
            for account in all_accounts:
                if await flood_manager.is_flooded(account["id"]):
                    continue
                task = asyncio.create_task(
                    self.worker(account, queue, callback)
                )
                workers.append(task)

            if not workers:
                # ── Auto-Recovery: جميع الحسابات في FloodWait ───────────
                logger.warning(
                    "[BatchChecker] جميع الحسابات محظورة مؤقتاً — "
                    "انتظار 30 ثانية ثم إعادة المحاولة تلقائياً..."
                )
                await _notify_admin(
                    "⏳ *تحذير الفاحص*\n"
                    "جميع حسابات الفحص في حالة FloodWait.\n"
                    "سيُعاد المحاولة تلقائياً بعد انتهاء مدة الحظر."
                )
                # انتظر 30 ثانية ثم أعد الفحص
                await asyncio.sleep(30)
                continue  # ← إعادة التحقق من الحسابات المتاحة

            # انتظار اكتمال هذه الجولة
            await queue.join()
            # إيقاف الـ workers بالترتيب
            for _ in workers:
                await queue.put(None)
            await asyncio.gather(*workers, return_exceptions=True)

            # إذا كان الطابور لا يزال يحتوي أرقاماً (أُعيدت بسبب FLOOD)،
            # تدور الحلقة مرة أخرى وتُعيد جلب الحسابات
            if not queue.empty():
                logger.info(
                    f"[BatchChecker] لا يزال في الطابور {queue.qsize()} رقم — "
                    f"إعادة توزيع على الحسابات المتاحة..."
                )
                await asyncio.sleep(5)  # توقف قصير قبل الجولة التالية

        return True


# الكائنات العامة
telegram_checker = TelegramChecker()
batch_checker = BatchChecker(telegram_checker)
