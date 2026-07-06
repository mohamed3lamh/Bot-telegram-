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
                await telegram_client_manager.disconnect_client(account["id"])
            except Exception:
                pass
            return {
                "status": "ERROR",
                "error": str(e),
                "phone": phone,
                "status_text": "⚪️ غير معروف / معلق"
            }

    async def get_available_account(self):
        """ الحصول على حساب متاح للفحص (التوزيع والفحص من FloodWait يتمّان داخل account_manager). """
        # account_manager.get_available_account() يُرجع فقط حسابات غير معطّلة وغير داخل FloodWait
        # (بالاعتماد على flood_until المخزَّن في نفس صف الحساب)، لذا لا حاجة لاستعلام DB إضافي هنا
        # يكرر نفس الفحص (كان يستدعي flood_manager.is_flooded مرة ثانية على نفس البيانات).
        account = await account_manager.get_available_account()
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
        """ عامل يستخدم حساب Telegram واحد لفحص الطابور بالتوازي. """
        try:
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
                    await callback(result)
                queue.task_done()
        finally:
            # هذا العامل خرج (لأي سبب: فراغ الطابور، Flood، أو تعطيل الحساب).
            # لو كان آخر عامل حي ولا يوجد من سيستهلك بقية الطابور، نُفرّغه فوراً
            # لمنع queue.join() من التعليق للأبد (Deadlock سابق كان يحدث هنا بالضبط).
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
                            f"BatchChecker: لا يوجد أي حساب فاحص متاح، تم تفريغ {drained} رقم من الطابور بدون فحص لتفادي تعليق النظام."
                        )

    async def run(self, phones, callback=None):
        """ تشغيل الفحص المتوازي الذكي. """
        queue = asyncio.Queue()
        for phone in phones:
            await queue.put(phone)

        # جلب الحسابات باستخدام الدالة الصحيحة
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
            # لا يوجد أي حساب فاحص متاح من البداية؛ لا فائدة من انتظار queue.join()
            # لأنه لن يوجد من يستهلك الطابور أبداً (كان هذا يسبب تعليقاً دائماً).
            logger.warning("BatchChecker.run: لا توجد حسابات فاحصة متاحة، تم إلغاء الفحص فوراً.")
            return False

        await queue.join()
        for _ in workers:
            await queue.put(None)
        await asyncio.gather(*workers, return_exceptions=True)
        return True

# بناء وإخراج الكائنات العامة للمشروع خارج الكلاسات (المسافات صفرية تماماً هنا)
telegram_checker = TelegramChecker()
batch_checker = BatchChecker(telegram_checker)
