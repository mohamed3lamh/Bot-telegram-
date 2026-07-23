import asyncio
from collections import defaultdict
import socks

from telegram_checker.backend.factory import BackendFactory, ACTIVE_ENGINE
from telegram_checker.backend.errors import BackendSessionUnauthorizedError
from telegram_checker.session import get_session_storage

class SessionUnauthorizedError(Exception):
    """Raised when the Telegram session is not authorized."""
    pass

class TelegramClientManager:
    def __init__(self):
        self.backends = {}
        self._locks = defaultdict(asyncio.Lock)
        self.session_storage = get_session_storage()

    async def get_client(self, account, proxy=None):
        account_id = account["id"]

        if proxy is not None:
            return await self._create_proxy_backend(account, proxy)

        async with self._locks[account_id]:
            if account_id in self.backends:
                backend = self.backends[account_id]
                try:
                    if not backend.is_connected():
                        await backend.connect()

                    if await backend.is_user_authorized():
                        return backend
                    else:
                        try:
                            await backend.disconnect()
                        except Exception:
                            pass
                        self.backends.pop(account_id, None)
                except Exception:
                    try:
                        await backend.disconnect()
                    except Exception:
                        pass
                    self.backends.pop(account_id, None)

            backend = await self._create_backend_instance(account, None)
            await backend.connect()
            
            if not await backend.is_user_authorized():
                await backend.disconnect()
                raise SessionUnauthorizedError("Telegram session is not authorized.")

            self.backends[account_id] = backend
            return backend

    async def _create_proxy_backend(self, account, proxy):
        backend = await self._create_backend_instance(account, proxy)
        await backend.connect()
        if not await backend.is_user_authorized():
            await backend.disconnect()
            raise SessionUnauthorizedError("Telegram session is not authorized (proxy client).")
        return backend

    async def _create_backend_instance(self, account, proxy):
        # We assume 'phone' is present. If not, fallback to 'session' logic mapping if possible.
        # It's better to ensure database.py queries fetch 'phone'.
        phone = account.get("phone", str(account["id"]))
        
        if ACTIVE_ENGINE == "tdlib":
            from telegram_checker.backend.tdlib_binding.core import TDLibClient
            tdlib_client = TDLibClient()
            tdlib_client.start()
            
            session_path = self.session_storage.get_session_path(phone)
            
            await tdlib_client.send({
                "@type": "setTdlibParameters",
                "use_test_dc": False,
                "database_directory": session_path,
                "use_file_database": False,
                "use_chat_info_database": False,
                "use_message_database": False,
                "api_id": int(account["api_id"]),
                "api_hash": account["api_hash"],
                "system_language_code": "en",
                "device_model": "SM-S918B",
                "system_version": "SDK 34",
                "application_version": "10.14.5",
                "enable_storage_optimizer": True
            })
            
            if proxy:
                proxy_type = proxy[0]
                host = proxy[1]
                port = proxy[2]
                username = proxy[4] if len(proxy) > 4 else ""
                password = proxy[5] if len(proxy) > 5 else ""
                
                tdlib_proxy_type = {"@type": "proxyTypeSocks5", "username": username, "password": password}
                if proxy_type == socks.HTTP:
                    tdlib_proxy_type = {"@type": "proxyTypeHttp", "username": username, "password": password, "http_only": False}
                
                await tdlib_client.send({
                    "@type": "addProxy",
                    "server": host,
                    "port": port,
                    "enable": True,
                    "type": tdlib_proxy_type
                })
            
            return BackendFactory.create_backend(tdlib_client)
            
        else:
            from telethon import TelegramClient
            from telethon.sessions import StringSession
            
            session_str = account["session"]
            if session_str == "tdlib_managed":
                raise ValueError("Cannot load TDLib session using Telethon engine.")
                
            client = TelegramClient(
                StringSession(session_str),
                int(account["api_id"]),
                account["api_hash"],
                proxy=proxy,
                device_model='SM-S918B',
                system_version='SDK 34',
                app_version='10.14.5',
                lang_code='en',
                system_lang_code='en-US'
            )
            return BackendFactory.create_backend(client)

    async def disconnect_client(self, account_id):
        backend = self.backends.pop(account_id, None)
        if backend:
            try:
                await backend.disconnect()
            except Exception:
                pass

    async def disconnect_all(self):
        for backend in list(self.backends.values()):
            try:
                await backend.disconnect()
            except Exception:
                pass
        self.backends.clear()

telegram_client_manager = TelegramClientManager()
