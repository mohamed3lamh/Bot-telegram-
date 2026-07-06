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

class TelegramChecker:
    def __init__(self):
        pass

    async def check_phone(self, account, phone):
        """ فحص حالة الرقم والجلسة بدقة متناهية بناءً على رد سيرفر التلغرام الفوري. """
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
            t_send_code_start = time.perf_counter()
            await client.send_code_request(phone)
            t_send_code_end = time.perf_counter()

            logger.info(
                f"[PERF_TRACE] [Checker ID: {account.get('id')}] send_code_request duration: "
                f"{t_send_code_end - t_send_code_start:.4f}s"
            )

            # لا يوجد خطأ = نعتبره "مسجل"
            return {
                "status": "REGISTERED",
                "phone": phone,
                "status_text": "📨 الرقم مسجل على تيليجرام"
            }

        # ✅ إضافة صارمة ومباشرة لحالة الرقم غير المسجل
        except PhoneNumberInvalidError as e:
            logger.warning(f"[INVALID] PHONE_NUMBER_INVALID detected: {phone} | {e}")
            return {
                "status": "INVALID",
                "phone": phone,
                "status_text": "❌ غير موجود / رقم غير صالح"
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
                "phone": phone
            }

        except Exception as e:
            logger.error("=" * 70)
            logger.error(f"UNKNOWN TELEGRAM ERROR أثناء فحص الرقم: {phone}")
            logger.error(f"TYPE : {type(e).__name__}")
            logger.error(f"REPR : {repr(e)}")
            logger.error(f"STR  : {str(e)}")
            logger.exception("FULL TRACEBACK")
            logger.error("=" * 70)

            try:
                await telegram_client_manager.disconnect_client(account["id"])
            except Exception:
                pass

            # مؤقتاً نُبقي نفس منطقك حتى نعرف الخطأ الحقيقي
            return {
                "status": "REGISTERED",
                "phone": phone,
                "status_text": "📨 الرقم مسجل على تيليجرام"
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
