import asyncio
from telethon import TelegramClient
from telethon.sessions import StringSession


class SessionUnauthorizedError(Exception):
    """Raised when the Telegram session is not authorized."""
    pass


class TelegramClientManager:
    def __init__(self):
        self.clients = {}

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

        if account_id in self.clients:
            client = self.clients[account_id]

            try:
                if not client.is_connected():
                    await asyncio.wait_for(client.connect(), timeout=15.0)

                if await asyncio.wait_for(client.is_user_authorized(), timeout=15.0):
                    return client
            except Exception:
                pass

        client = TelegramClient(
            StringSession(account["session"]),
            int(account["api_id"]),
            account["api_hash"]
        )

        await asyncio.wait_for(client.connect(), timeout=15.0)

        if not await asyncio.wait_for(client.is_user_authorized(), timeout=15.0):
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
