import asyncio
import os
import logging
from telethon import functions, types
from telethon.errors import (
    FloodWaitError, UserPrivacyRestrictedError, PhoneNumberBannedError,
    PhoneNumberInvalidError,
    PhoneMigrateError, NetworkMigrateError, UserMigrateError,
    PhoneNumberUnoccupiedError
)
from .telegram_client import telegram_client_manager, SessionUnauthorizedError
from .account_manager import account_manager
from .flood_manager import flood_manager

logger = logging.getLogger(__name__)

class TelegramChecker:
    def __init__(self):
        self._fast_check_fails = {}  # {account_id: count} تتبع فشل الفحص السريع

    async def _migrate_dc(self, client, account_id, phone, new_dc):
        """ترحيل client إلى DC جديد والمحاولة مرة أخرى"""
        try:
            await client.disconnect()
            for dc_opt in getattr(client.session, 'dc_options', []):
                if getattr(dc_opt, 'id', None) == new_dc:
                    client.session.set_dc(dc_opt.id, dc_opt.ip_address, dc_opt.port)
                    break
            await client.connect()
            await asyncio.wait_for(client.send_code_request(phone), timeout=15.0)
            return True
        except Exception:
            try:
                await telegram_client_manager.disconnect_client(account_id)
            except Exception:
                pass
            return False

    async def check_phone(self, account, phone):
        """فحص الرقم: المستوى 1 سريع (CheckPhoneRequest) → المستوى 2 كامل (send_code_request)"""
        import time
        try:
            client = await telegram_client_manager.get_client(account)
        except SessionUnauthorizedError:
            await account_manager.disable_account(account["id"])
            return {"status": "ACCOUNT_DISABLED", "phone": phone, "status_text": "❌ حساب الفاحص تالف"}

        # ===== المستوى 1: فحص سريع بدون إرسال SMS =====
        try:
            t0 = time.perf_counter()
            result = await client(functions.contacts.CheckPhoneRequest(phone_number=phone))
            elapsed = time.perf_counter() - t0
            registered = getattr(result, 'phone_registered', None)
            if registered is not None:
                # Reset fail counter on success
                self._fast_check_fails[account["id"]] = 0
                logger.info(f"[Checker] #{account['id']}: FastCheck {phone}: registered={registered} ({elapsed:.3f}s)")
                if registered:
                    return {"status": "HAS_SESSION", "phone": phone, "status_text": "⚠️ الرقم لديه جلسة"}
                else:
                    return {"status": "NO_SESSION", "phone": phone, "status_text": "✅ الرقم بدون جلسة"}
        except Exception as e:
            fail_count = self._fast_check_fails.get(account["id"], 0) + 1
            self._fast_check_fails[account["id"]] = fail_count
            logger.info(f"[Checker] #{account['id']}: FastCheck فشل ({fail_count}): {type(e).__name__}")
            if fail_count > 5:
                logger.warning(f"[Checker] #{account['id']}: فشل الفحص السريع 5+ مرات متتالية، تعطيله")

        # ===== المستوى 2: فحص كامل عبر send_code_request =====
        try:
            await asyncio.wait_for(client.send_code_request(phone), timeout=15.0)
            # نجح الإرسال → الرقم شغال
            # نتحقق من التسجيل
            try:
                result = await client(functions.contacts.CheckPhoneRequest(phone_number=phone))
                registered = getattr(result, 'phone_registered', None)
            except Exception:
                registered = None
            if registered:
                return {"status": "HAS_SESSION", "phone": phone, "status_text": "⚠️ الرقم لديه جلسة"}
            return {"status": "NO_SESSION", "phone": phone, "status_text": "✅ الرقم بدون جلسة"}

        except PhoneNumberUnoccupiedError:
            return {"status": "NO_SESSION", "phone": phone, "status_text": "✅ الرقم بدون جلسة"}
        except PhoneNumberBannedError:
            return {"status": "BANNED", "phone": phone, "status_text": "📵 مـحـظـور"}
        except PhoneNumberInvalidError:
            return {"status": "INVALID", "phone": phone, "status_text": "⚠️ رقم غير صالح"}
        except FloodWaitError as e:
            await flood_manager.set_flood(account["id"], e.seconds)
            return {"status": "FLOOD", "seconds": e.seconds, "phone": phone, "status_text": f"🚫 حظر مؤقت {e.seconds}ث"}

        except (PhoneMigrateError, NetworkMigrateError, UserMigrateError) as e:
            new_dc = getattr(e, 'new_dc', '?')
            logger.info(f"[Checker] #{account['id']}: {phone} DC migration → {new_dc}")
            ok = await self._migrate_dc(client, account["id"], phone, new_dc)
            if not ok:
                return {"status": "ERROR", "phone": phone, "status_text": "⚠️ تعذر فحص الرقم (DC)"}
            try:
                result = await client(functions.contacts.CheckPhoneRequest(phone_number=phone))
                registered = getattr(result, 'phone_registered', None)
            except Exception:
                registered = None
            if registered:
                return {"status": "HAS_SESSION", "phone": phone, "status_text": "⚠️ الرقم لديه جلسة"}
            return {"status": "NO_SESSION", "phone": phone, "status_text": "✅ الرقم بدون جلسة"}

        except Exception as e:
            try:
                await telegram_client_manager.disconnect_client(account["id"])
            except Exception:
                pass
            return {"status": "ERROR", "phone": phone, "status_text": "⚠️ فشل الفحص"}

    async def get_available_accounts(self):
        """جلب جميع الحسابات المتاحة (غير المغمورة)"""
        accounts = await account_manager.get_all_accounts()
        available = []
        for acc in accounts:
            if not await flood_manager.is_flooded(acc["id"]):
                fc = self._fast_check_fails.get(acc["id"], 0)
                if fc <= 5:
                    available.append(acc)
                else:
                    logger.warning(f"[Checker] #{acc['id']}: متجاوز بسبب فشل الفحص السريع ({fc})")
        return available

    async def get_available_account(self):
        accs = await self.get_available_accounts()
        return accs[0] if accs else None

    async def wait_for_account(self):
        while True:
            acc = await self.get_available_account()
            if acc:
                return acc
            await asyncio.sleep(3)

    async def check_numbers_batch(self, phones_list, callback=None):
        """فحص مجموعة أرقام بالتوازي باستخدام كل الحسابات المتاحة"""
        if not phones_list:
            return
        accounts = await self.get_available_accounts()
        if not accounts:
            logger.warning("[Batch] لا توجد حسابات فاحصة متاحة")
            return

        q = asyncio.Queue()
        for p in phones_list:
            await q.put(p)

        async def worker(acc):
            while True:
                try:
                    phone = await asyncio.wait_for(q.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    break
                if phone is None:
                    q.task_done()
                    break
                try:
                    result = await self.check_phone(acc, phone)
                    if result["status"] == "FLOOD":
                        await flood_manager.set_flood(acc["id"], result.get("seconds", 60))
                        await q.put(phone)
                    elif result["status"] == "ACCOUNT_DISABLED":
                        await q.put(phone)
                    elif callback:
                        try:
                            await callback(result)
                        except Exception:
                            pass
                except Exception:
                    pass
                q.task_done()

        workers = [asyncio.create_task(worker(acc)) for acc in accounts[:5]]
        await q.join()
        for _ in workers:
            await q.put(None)
        await asyncio.gather(*workers, return_exceptions=True)

    async def check_numbers(self, phones, callback=None):
        for phone in phones:
            account = await self.wait_for_account()
            result = await self.check_phone(account, phone)
            if result["status"] in ["FLOOD", "ACCOUNT_DISABLED"]:
                continue
            if callback:
                await callback(result)

class BatchChecker:
    def __init__(self, checker):
        self.checker = checker

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
                except Exception:
                    pass
            queue.task_done()

    async def run(self, phones, callback=None):
        queue = asyncio.Queue()
        for phone in phones:
            await queue.put(phone)
        accounts = await account_manager.get_all_accounts()
        workers = []
        for account in accounts:
            if await flood_manager.is_flooded(account["id"]):
                continue
            task = asyncio.create_task(self.worker(account, queue, callback))
            workers.append(task)
        await queue.join()
        for _ in workers:
            await queue.put(None)
        await asyncio.gather(*workers, return_exceptions=True)
        return True

telegram_checker = TelegramChecker()
batch_checker = BatchChecker(telegram_checker)