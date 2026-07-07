import asyncio
import os
import logging
import time
from telethon import functions, types
from telethon.errors import (
    FloodWaitError, UserPrivacyRestrictedError, PhoneNumberBannedError,
    SessionPasswordNeededError, PhoneNumberInvalidError,
    PhoneNumberUnoccupiedError, PhoneMigrateError  # ⚠️ تم ضبط الاستيرادات بشكل سليم
)
from .telegram_client import telegram_client_manager, SessionUnauthorizedError
from .account_manager import account_manager
from .flood_manager import flood_manager

logger = logging.getLogger(__name__)

class TelegramChecker:
    def __init__(self):
        pass

    async def check_phone(self, account, phone):
        """ فحص حالة الرقم والجلسة بدقة متناهية بناءً على رد سيرفر التلغرام الفوري. """
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
            # 1. التحقق أولاً من تسجيل الرقم عبر استيراد جهة الاتصال
            # لتفادي إرسال كود للأرقام المسجلة مسبقاً وتوفير التكلفة والـ Spam limit
            is_registered = False
            try:
                contact = types.InputPhoneContact(client_id=0, phone=phone, first_name="TempCheck", last_name="")
                import_res = await asyncio.wait_for(
                    client(functions.contacts.ImportContactsRequest(contacts=[contact])),
                    timeout=10.0
                )
                if import_res.users:
                    is_registered = True
                    # حذف جهة الاتصال فوراً لتفادي تراكم جهات الاتصال في الحساب الفاحص
                    user_id = import_res.users[0].id
                    await client(functions.contacts.DeleteContactsRequest(id=[user_id]))
            except Exception as e:
                logger.warning(f"Contact import check failed for {phone}: {e}")

            if is_registered:
                return {
                    "status": "REGISTERED",
                    "phone": phone,
                    "status_text": "⚠️ مسجل"
                }

            # 2. إذا لم يكن مسجلاً، نقوم بإرسال الكود لتنشيط الـ SMS الفعلي وتأكيد الحالة
            t_send_code_start = time.perf_counter()
            await client.send_code_request(phone)
            t_send_code_end = time.perf_counter()

            logger.info(
                f"[PERF_TRACE] [Checker ID: {account.get('id')}] send_code_request duration: "
                f"{t_send_code_end - t_send_code_start:.4f}s"
            )

            # لا يوجد خطأ، وكان غير مسجل في فحص جهات الاتصال،
            # فهذا يعني أن الرقم جديد تماماً وغير مسجل وتم إرسال كود SMS حقيقي له.
            return {
                "status": "NOT_REGISTERED",
                "phone": phone,
                "status_text": "🆕 غير مسجل"
            }

        except SessionPasswordNeededError:
            # الرقم لديه جلسة محمية بالتحقق بخطوتين (مسجل)
            return {
                "status": "REGISTERED",
                "phone": phone,
                "status_text": "⚠️ مسجل"
            }

        except PhoneNumberUnoccupiedError:
            # الرقم غير مسجل في تليجرام
            return {
                "status": "NOT_REGISTERED",
                "phone": phone,
                "status_text": "🆕 غير مسجل"
            }

        except PhoneMigrateError as e:
            logger.warning(f"🔄 الرقم {phone} ينتمي إلى مركز البيانات DC {e.dc}. جاري التوجيه...")
            try:
                client = await telegram_client_manager.get_client(account)
                await client._switch_dc(e.dc)
                await asyncio.sleep(0.5)
                return await self.check_phone(account, phone)
            except Exception as migrate_error:
                logger.error(f"فشل التحويل التلقائي لـ DC {e.dc}: {migrate_error}")
                return {
                    "status": "MIGRATE_FAILED",
                    "phone": phone,
                    "status_text": f"❌ فشل الاتصال بـ DC {e.dc}"
                }

        except PhoneNumberInvalidError:
            return {
                "status": "INVALID",
                "phone": phone,
                "status_text": "⚠️ غير صالح"
            }

        except PhoneNumberBannedError:
            return {
                "status": "BANNED",
                "phone": phone,
                "status_text": "📵 محظور"
            }

        except FloodWaitError as e:
            await flood_manager.set_flood(account["id"], e.seconds)
            return {
                "status": "FLOOD",
                "seconds": e.seconds,
                "phone": phone,
                "status_text": f"🚫 حظر مؤقت {e.seconds} ثانية"
            }
            
        except Exception as e:
            try:
                await telegram_client_manager.disconnect_client(account["id"])
            except Exception:
                pass

            # 🛠️ تعديل هندسي: استخراج نص الخطأ الصريح القادم من تيليجرام لمعرفة السبب بدقة
            error_message = str(e)
            
            # إذا كان الخطأ بسبب حظر حساب الفاحص نفسه
            if "BANNED" in error_message.upper() or "AUTH_KEY_UNREGISTERED" in error_message.upper():
                await account_manager.disable_account(account["id"])
                return {
                    "status": "CHECKER_BANNED",
                    "phone": phone,
                    "status_text": "❌ حساب الفاحص نفسه تم حظره الآن وتلف"
                }

            return {
                "status": "UNKNOWN_ERROR",
                "phone": phone,
                "status_text": f"⚙️ خطأ من السيرفر: {error_message}"  # سيطبع لك نص الخطأ الحقيقي في التلغرام
            }

    async def get_available_account(self):
        account = await account_manager.get_available_account()
        return account

    async def wait_for_account(self):
        while True:
            account = await self.get_available_account()
            if account:
                return account
            await asyncio.sleep(5)

    async def check_numbers(self, phones, callback=None):
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

    async def worker(self, account, queue, callback=None, active_workers=None):
        try:
            while True:
                phone = await queue.get()

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
