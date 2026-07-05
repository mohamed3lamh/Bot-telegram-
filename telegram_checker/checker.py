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
        """فحص حالة الرقم بشكل دقيق مع التمييز بين بدون جلسة ولديه جلسة"""
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
            # الخطوة 1: التحقق مما إذا كان الرقم مسجلاً على تيليجرام (بدون إرسال كود)
            try:
                result = await client(
                    functions.auth.CheckPasswordRequest(phone)
                )
            except Exception:
                # في بعض الإصدارات، الدالة الصحيحة هي CheckPhoneRequest
                pass
            
            # استخدام CheckPhoneRequest للتحقق من وجود الحساب
            try:
                check_result = await client(
                    functions.auth.CheckPhoneRequest(phone)
                )
                # إذا وصلنا هنا بدون خطأ، فالرقم مسجل (لديه جلسة)
                # الآن نتحقق مما إذا كان يتطلب 2FA
                try:
                    await client.send_code_request(phone)
                    await self._safe_disconnect(client)
                    return {
                        "status": "HAS_SESSION",
                        "phone": phone,
                        "status_text": "🔐 لديه جلسة"
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
                except Exception:
                    await self._safe_disconnect(client)
                    return {
                        "status": "HAS_SESSION",
                        "phone": phone,
                        "status_text": "🔐 لديه جلسة"
                    }
            except PhoneNumberUnoccupiedError:
                # الرقم غير مسجل (بدون جلسة)
                await self._safe_disconnect(client)
                return {
                    "status": "NO_SESSION",
                    "phone": phone,
                    "status_text": "✅ بدون جلسة"
                }
            except Exception:
                # فشل CheckPhoneRequest، نحاول إرسال كود
                pass

            # الخطوة 2: إذا فشلت الخطوة السابقة، نستخدم send_code_request كالعادة
            await asyncio.wait_for(
                client.send_code_request(phone),
                timeout=REQUEST_TIMEOUT_SECONDS
            )

            await self._safe_disconnect(client)

            return {
                "status": "CODE_SENT",
                "phone": phone,
                "status_text": "📨 تم إرسال كود التحقق (رقم موجود أو قابل للتسجيل)"
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

            if "PHONE_NUMBER_OCCUPIED" in err_text:
                status = "HAS_SESSION"
                status_text = "🔐 لديه جلسة"
            elif "FLOOD" in err_text:
                status = "FLOOD"
                status_text = None
            elif "PHONE_NUMBER_UNOCCUPIED" in err_text:
                status = "NO_SESSION"
                status_text = "✅ بدون جلسة"
            else:
                status = "ERROR"
                status_text = "⚪️ غير معروف / معلق"

            return {
                "status": status,
                "error": err_text,
                "phone": phone,
                "status_text": status_text
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
