from telethon import TelegramClient
from telethon.sessions import StringSession
import asyncio
from collections import defaultdict


class SessionUnauthorizedError(Exception):
    """Raised when the Telegram session is not authorized."""
    pass


class TelegramClientManager:
    def __init__(self):
        self.clients = {}
        # قفل مستقل لكل account_id يمنع إنشاء عدة اتصالات Telethon متزامنة لنفس الحساب
        # (Race Condition عند أول استخدام لحساب لم يُخزَّن بعد في self.clients)
        self._locks = defaultdict(asyncio.Lock)

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

        async with self._locks[account_id]:
            if account_id in self.clients:
                client = self.clients[account_id]

                try:
                    if not client.is_connected():
                        await client.connect()

                    if await client.is_user_authorized():
                        return client
                    else:
                        # غير مخول، نقوم بقطع اتصاله لعدم تسريب المقبس
                        try:
                            await client.disconnect()
                        except Exception:
                            pass
                        self.clients.pop(account_id, None)
                except Exception:
                    # أي استثناء آخر (فشل شبكة، إلخ)، نقوم بقطع الاتصال وإزالته من الكاش لإعادة المحاولة من جديد
                    try:
                        await client.disconnect()
                    except Exception:
                        pass
                    self.clients.pop(account_id, None)

            client = TelegramClient(
                StringSession(account["session"]),
                int(account["api_id"]),
                account["api_hash"]
            )

            await client.connect()

            if not await client.is_user_authorized():
                await client.disconnect()
                raise SessionUnauthorizedError(
                    "Telegram session is not authorized."
                )

            self.clients[account_id] = client
            return client

    async def disconnect_client(self, account_id):
        client = self.clients.pop(account_id, None)

        if client is None:
            return

        try:
            await client.disconnect()
        except Exception:
            pass

    async def disconnect_all(self):
        for client in list(self.clients.values()):
            try:
                await client.disconnect()
            except Exception:
                pass

        self.clients.clear()


telegram_client_manager = TelegramClientManager()
