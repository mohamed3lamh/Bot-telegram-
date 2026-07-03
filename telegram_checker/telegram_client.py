import asyncio
import logging
from telethon import TelegramClient
from telethon.sessions import StringSession

logger = logging.getLogger(__name__)

class SessionUnauthorizedError(Exception):
    """Raised when the Telegram session is not authorized."""
    pass


class TelegramClientManager:
    def __init__(self):
        self.clients = {}
        self.locks = {}  # قفل لكل حساب لتفادي التعارض وتداخل الاتصالات

    def get_lock(self, account_id):
        if account_id not in self.locks:
            self.locks[account_id] = asyncio.Lock()
        return self.locks[account_id]

    async def get_client(self, account):
        """
        account = {
            "id": 1,
            "api_id": 12345,
            "api_hash": "xxxxx",
            "session": "xxxxx"
        }
        """
        account_id = account["id"]
        lock = self.get_lock(account_id)

        async with lock:
            if account_id in self.clients:
                client = self.clients[account_id]

                try:
                    # فحص الاتصال مع مهلة زمنية 15 ثانية لتفادي التعليق
                    if not client.is_connected():
                        await asyncio.wait_for(client.connect(), timeout=15.0)

                    # التحقق من صلاحية الجلسة مع مهلة 15 ثانية
                    if await asyncio.wait_for(client.is_user_authorized(), timeout=15.0):
                        return client
                except Exception as e:
                    logger.warning(
                        f"[Client Manager] فشل الاتصال المخزن للحساب {account_id}: {e}. جاري التنظيف..."
                    )
                    try:
                        await asyncio.wait_for(client.disconnect(), timeout=5.0)
                    except Exception:
                        pass
                    self.clients.pop(account_id, None)

            # إنشاء كائن عميل تليجرام جديد
            logger.info(f"[Client Manager] إنشاء اتصال جديد للحساب {account_id}...")
            client = TelegramClient(
                StringSession(account["session"]),
                int(account["api_id"]),
                account["api_hash"]
            )

            try:
                # محاولة الاتصال وتأكيد الجلسة مع مهلة 15 ثانية
                await asyncio.wait_for(client.connect(), timeout=15.0)
                is_auth = await asyncio.wait_for(client.is_user_authorized(), timeout=15.0)
                if not is_auth:
                    await asyncio.wait_for(client.disconnect(), timeout=5.0)
                    raise SessionUnauthorizedError(
                        "Telegram session is not authorized."
                    )
            except Exception as e:
                logger.error(
                    f"[Client Manager] فشل تهيئة الاتصال الجديد للحساب {account_id}: {e}"
                )
                # ضمان إغلاق الاتصال التالف لمنع تسريب المقابس والذاكرة
                try:
                    await asyncio.wait_for(client.disconnect(), timeout=5.0)
                except Exception:
                    pass
                if isinstance(e, SessionUnauthorizedError):
                    raise
                raise Exception(f"Failed to connect client: {e}")

            self.clients[account_id] = client
            return client

    async def disconnect_client(self, account_id):
        lock = self.get_lock(account_id)
        async with lock:
            client = self.clients.pop(account_id, None)
            if client is None:
                return

            try:
                await asyncio.wait_for(client.disconnect(), timeout=5.0)
            except Exception as e:
                logger.warning(
                    f"[Client Manager] خطأ أثناء فصل الحساب {account_id}: {e}"
                )

    async def disconnect_all(self):
        for account_id in list(self.clients.keys()):
            await self.disconnect_client(account_id)
        self.clients.clear()


telegram_client_manager = TelegramClientManager()
