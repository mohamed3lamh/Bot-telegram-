import asyncio
import logging
from contextlib import suppress
from telethon import functions, types
from telethon.errors import (
    FloodWaitError, UserPrivacyRestrictedError, PhoneNumberBannedError,
    SessionPasswordNeededError, PhoneNumberInvalidError,
    PhoneNumberUnoccupiedError
)
from .telegram_client import telegram_client_manager, SessionUnauthorizedError
from .account_manager import account_manager
from .flood_manager import flood_manager

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 45


class TelegramChecker:
    def __init__(self):
        pass

    async def _safe_disconnect(self, client):
        with suppress(Exception):
            await client.disconnect()

    async def check_phone(self, account, phone):
        """فحص حالة الرقم بدقة: بدون جلسة، لديه جلسة، محظور، إلخ."""
        try:
            client = await telegram_client_manager.get_client(account)

        except SessionUnauthorizedError:
            await account_manager.disable_account(account["id"])
            return {
                "status": "ACCOUNT_DISABLED",
                "phone": phone,
                "status_text": "❌ حساب الفاحص تالف وتم تعطيله"
            }

        try:
            # الخطوة 1: التحقق من وجود الحساب على تيليجرام باستخدام get_entity
            try:
                entity = await client.get_entity(phone)
                # إذا نجح، الرقم مسجل (لديه جلسة)
                is_registered = True
            except (ValueError, PhoneNumberUnoccupiedError):
                # الرقم غير مسجل (بدون جلسة)
                is_registered = False
            except Exception as e:
                # خطأ غير متوقع في get_entity، نحاول send_code_request كبديل
                is_registered = None

            if is_registered is False:
                # بالتأكيد بدون جلسة
                await self._safe_disconnect(client)
                return {
                    "status": "NO_SESSION",
                    "phone": phone,
                    "status_text": "✅ بدون جلسة"
                }

            # الخطوة 2: إذا كان مسجلاً أو غير معروف، أرسل كود التحقق لتصنيف إضافي
            try:
                await asyncio.wait_for(
                    client.send_code_request(phone),
                    timeout=REQUEST_TIMEOUT_SECONDS
                )
                # إذا وصلنا هنا، تم إرسال الكود بنجاح
                await self._safe_disconnect(client)

                if is_registered is True:
                    # مسجل وتم إرسال الكود -> لديه جلسة
                    return {
                        "status": "HAS_SESSION",
                        "phone": phone,
                        "status_text": "🔐 لديه جلسة"
                    }
                else:
                    # غير معروف التسجيل لكن تم إرسال الكود (نادر)
                    return {
                        "status": "CODE_SENT",
                        "phone": phone,
                        "status_text": "📨 تم إرسال كود التحقق"
                    }

            except PhoneNumberUnoccupiedError:
                await self._safe_disconnect(client)
                return {
                    "status": "NO_SESSION",
                    "phone": phone,
                    "status_text": "✅ بدون جلسة"
                }

            except SessionPasswordNeededError:
                await self._safe_disconnect(client)
                return {
                    "status": "REGISTERED_2FA",
                    "phone": phone,
                    "status_text": "🔐 لديه جلسة (2FA)"
                }

            except PhoneNumberBannedError:
                await self._safe_disconnect(client)
                return {
                    "status": "BANNED",
                    "phone": phone,
                    "status_text": "🚯 محظور"
                }

            except PhoneNumberInvalidError:
                await self._safe_disconnect(client)
                return {
                    "status": "INVALID",
                    "phone": phone,
                    "status_text": "⚠️ رقم غير صالح"
                }

            except FloodWaitError as e:
                await flood_manager.set_flood(account["id"], e.seconds)
                await self._safe_disconnect(client)
                return {
                    "status": "FLOOD",
                    "seconds": e.seconds,
                    "phone": phone,
                    "status_text": f"⏳ Flood Wait {e.seconds}s"
                }

            except asyncio.TimeoutError:
                await self._safe_disconnect(client)
                return {
                    "status": "ERROR",
                    "phone": phone,
                    "error": "request_timeout",
                    "status_text": "⏳ انتهت مهلة الفحص"
                }

            except Exception as e:
                await self._safe_disconnect(client)
                err_text = str(e)
                if "PHONE_NUMBER_UNOCCUPIED" in err_text:
                    return {
                        "status": "NO_SESSION",
                        "phone": phone,
                        "status_text": "✅ بدون جلسة"
                    }
                elif "PHONE_NUMBER_OCCUPIED" in err_text:
                    return {
                        "status": "HAS_SESSION",
                        "phone": phone,
                        "status_text": "🔐 لديه جلسة"
                    }
                else:
                    return {
                        "status": "ERROR",
                        "error": err_text,
                        "phone": phone,
                        "status_text": "⚪️ غير معروف / معلق"
                    }

        except Exception as e:
            # خطأ عام
            await self._safe_disconnect(client)
            return {
                "status": "ERROR",
                "error": str(e),
                "phone": phone,
                "status_text": "⚪️ غير معروف / معلق"
            }

    async def get_available_account(self):
        account = await account_manager.get_available_account()

        if not account:
            return None
        if await flood_manager.is_flooded(account["id"]):
            return None
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

    async def _safe_queue_put(self, queue, phone):
        try:
            queue.put_nowait(phone)
        except asyncio.QueueFull:
            await queue.put(phone)

    async def worker(self, account, queue, callback=None):
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
                await self._safe_queue_put(queue, phone)
                queue.task_done()
                break

            elif result["status"] == "ACCOUNT_DISABLED":
                await self._safe_queue_put(queue, phone)
                queue.task_done()
                break

            if callback:
                await callback(result)

            queue.task_done()

    async def run(self, phones, callback=None):
        queue = asyncio.Queue()

        for phone in phones:
            await queue.put(phone)

        accounts = await account_manager.get_all_accounts()
        if not accounts:
            return False

        workers = []
        for account in accounts:
            if await flood_manager.is_flooded(account["id"]):
                continue

            task = asyncio.create_task(
                self.worker(account, queue, callback)
            )
            workers.append(task)

        if not workers:
            return False

        timed_out = False

        try:
            await asyncio.wait_for(queue.join(), timeout=300)
        except asyncio.TimeoutError:
            timed_out = True
            logger.warning("BatchChecker timed out waiting for queue completion")

        finally:
            if timed_out:
                for task in workers:
                    task.cancel()
            else:
                for _ in workers:
                    await queue.put(None)

            await asyncio.gather(*workers, return_exceptions=True)

        return queue.empty() and not timed_out


telegram_checker = TelegramChecker()
batch_checker = BatchChecker(telegram_checker)
