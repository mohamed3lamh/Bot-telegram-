import asyncio
import os
import logging
from telethon import functions, types
from telethon.errors import (
    FloodWaitError, UserPrivacyRestrictedError, PhoneNumberBannedError,
    SessionPasswordNeededError, PhoneNumberInvalidError
)
from .telegram_client import telegram_client_manager
from .account_manager import account_manager
from .flood_manager import flood_manager

logger = logging.getLogger(__name__)

class TelegramChecker:
    def __init__(self):
        pass

    async def check_phone(self, account, phone):
        """ فحص حالة الرقم والجلسة بدقة متناهية بناءً على رد سيرفر التلغرام الفوري. """
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
            # محاولة إرسال طلب الكود للرقم لمعرفة حالته وجلسته فوراً
            await client.send_code_request(phone)
            await client.disconnect()
            # إذا مر السطر السابق بدون أخطاء، فالرقم مفتوح وجاهز تماماً بدون باسورد
            return {
                "status": "NO_SESSION",
                "phone": phone,
                "status_text": "✅ الرقم بدون جلسة"
            }
        except SessionPasswordNeededError:
            await client.disconnect()
            # الرقم شغال وموجود ولكن صاحبه وضع كلمة سر التحقق بخطوتين
            return {
                "status": "HAS_SESSION",
                "phone": phone,
                "status_text": "⚠️ الرقم لديه جلسة"
            }
        except PhoneNumberBannedError:
            await client.disconnect()
            # الرقم طار وتم حظره من شركة التلغرام تماماً
            return {
                "status": "BANNED",
                "phone": phone,
                "status_text": "🚯 محظور"
            }
        except PhoneNumberInvalidError:
            await client.disconnect()
            return {
                "status": "INVALID",
                "phone": phone,
                "status_text": "⚠️ رقم غير صالح"
            }
        except FloodWaitError as e:
            # في حال واجه الحساب الفاحص حظر مؤقت (سبام) من كثرة الفحص
            await flood_manager.set_flood(account["id"], e.seconds)
            await client.disconnect()
            return {
                "status": "FLOOD",
                "seconds": e.seconds,
                "phone": phone
            }
        except Exception as e:
            await client.disconnect()
            return {
                "status": "ERROR",
                "error": str(e),
                "phone": phone,
                "status_text": "⚪️ غير معروف / معلق"
            }

    async def get_available_account(self):
        """ الحصول على أول حساب متاح للفحص. """
        # استخدام الدالة الصحيحة بالمفرد كما هو معرف في AccountManager
        account = await account_manager.get_available_account()
            
        if not account:
            return None
        if await flood_manager.is_flooded(account["id"]):
            return None
        return account

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
            if result["status"] == "FLOOD":
                continue
            if result["status"] == "BANNED":
                await account_manager.disable_account(account["id"])
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
            elif result["status"] == "BANNED":
                await account_manager.disable_account(account["id"])
                queue.put_nowait(phone)
                queue.task_done()
                break
                
            if callback:
                await callback(result)
            queue.task_done()

    async def run(self, phones, callback=None):
        """ تشغيل الفحص المتوازي الذكي. """
        queue = asyncio.Queue()
        for phone in phones:
            await queue.put(phone)
            
        # جلب الحسابات باستخدام الدالة الصحيحة
        accounts = await account_manager.get_all_accounts()
        workers = []
        for account in accounts:
            if await flood_manager.is_flooded(account["id"]):
                continue
            task = asyncio.create_task(
                self.worker(account, queue, callback)
            )
            workers.append(task)
            
        await queue.join()
        for _ in workers:
            await queue.put(None)
        await asyncio.gather(*workers, return_exceptions=True)
        return True

# بناء وإخراج الكائنات العامة للمشروع خارج الكلاسات (المسافات صفرية تماماً هنا)
telegram_checker = TelegramChecker()
batch_checker = BatchChecker(telegram_checker)
