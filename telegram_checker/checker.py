import asyncio
import os
import logging
from telethon import functions, types
from telethon.errors import (
    FloodWaitError, UserPrivacyRestrictedError, PhoneNumberBannedError,
    SessionPasswordNeededError, PhoneNumberInvalidError
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
        t_start = time.perf_counter()
        try:
            t_get_client_start = time.perf_counter()
            client = await telegram_client_manager.get_client(account)
            t_get_client_end = time.perf_counter()
            logger.info(
                f"[PERF_TRACE] [Checker ID: {account.get('id')}] get_client duration: "
                f"{t_get_client_end - t_get_client_start:.4f}s"
            )
        except SessionUnauthorizedError:
            await account_manager.disable_account(account["id"])
            return {
                "status": "ACCOUNT_DISABLED",
                "phone": phone,
                "status_text": "❌ حساب الفاحص تالف وتم تعطيله"
            }
        try:
            # محاولة إرسال طلب الكود للرقم لمعرفة حالته وجلسته فوراً
            t_send_code_start = time.perf_counter()
            await client.send_code_request(phone)
            t_send_code_end = time.perf_counter()
            logger.info(
                f"[PERF_TRACE] [Checker ID: {account.get('id')}] send_code_request duration: "
                f"{t_send_code_end - t_send_code_start:.4f}s"
            )
            # إذا مر السطر السابق بدون أخطاء، فالرقم مفتوح وجاهز تماماً بدون باسورد
            return {
                "status": "NO_SESSION",
                "phone": phone,
                "status_text": "✅ الرقم بدون جلسة"
            }
        except SessionPasswordNeededError:
            # الرقم شغال وموجود ولكن صاحبه وضع كلمة سر التحقق بخطوتين
            return {
                "status": "HAS_SESSION",
                "phone": phone,
                "status_text": "⚠️ الرقم لديه جلسة"
            }
        except PhoneNumberBannedError:
            # الرقم طار وتم حظره من شركة التلغرام تماماً
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
        except FloodWaitError as e:
            # في حال واجه الحساب الفاحص حظر مؤقت (سبام) من كثرة الفحص
            await flood_manager.set_flood(account["id"], e.seconds)
            return {
                "status": "FLOOD",
                "seconds": e.seconds,
                "phone": phone
            }
        except Exception as e:
            # في حالة حدوث خطأ غير متوقع، قد يكون الاتصال قد تضرر، فنفصله للتأكد من إعادة بنائه في المرة القادمة
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

    async def wait_for_account(self):
        """ الانتظار حتى يصبح هناك حساب متاح. """
        while True:
            account = await self.get_available_account()
            if account:
                return account
            await asyncio.sleep(5)

    async def check_numbers(self, phones, callback=None):
        """ فحص مجموعة أرقام. """
        results = []
        for phone in phones:
            account = await self.wait_for_account()
            result = await self.check_phone(account, phone)
            if result["status"] in ["FLOOD", "ACCOUNT_DISABLED"]:
                continue
            results.append(result)
            if callback:
                await callback(result)
        return results

class BatchChecker:
    def __init__(self, checker):
        self.checker = checker

    async def worker(self, account, queue, callback=None):
        """ عامل يستخدم حساب Telegram واحد لفحص الطابور بالتوازي. """
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
            elif result["status"] == "ACCOUNT_DISABLED":
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

# بناء وإخراج الكائنات العامة للمشروع خارج الكلاسات (المسافات صفرية تماماً هنا)
telegram_checker = TelegramChecker()
batch_checker = BatchChecker(telegram_checker)
