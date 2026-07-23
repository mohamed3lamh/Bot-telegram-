import asyncio
import unittest
from unittest.mock import patch, AsyncMock, MagicMock
import os
import socks

from telegram_checker.telegram_client import telegram_client_manager, SessionUnauthorizedError
from telegram_checker.backend.factory import ACTIVE_ENGINE

class TestTelegramClientManager(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.manager = telegram_client_manager
        self.manager.backends.clear()
        
        self.account_telethon = {
            "id": 1,
            "phone": "+111",
            "api_id": 123,
            "api_hash": "hash",
            "session": "stringsession_base64"
        }
        self.account_tdlib = {
            "id": 2,
            "phone": "+222",
            "api_id": 123,
            "api_hash": "hash",
            "session": "tdlib_managed"
        }

    @patch("telegram_checker.telegram_client.BackendFactory.create_backend")
    @patch("telethon.TelegramClient")
    async def test_get_client_telethon_success(self, mock_telethon_cls, mock_factory):
        # Mock engine
        import telegram_checker.telegram_client as tc
        tc.ACTIVE_ENGINE = "telethon"
        
        mock_backend = AsyncMock()
        mock_backend.is_user_authorized.return_value = True
        mock_backend.is_connected.return_value = True
        mock_factory.return_value = mock_backend
        
        client = await self.manager.get_client(self.account_telethon)
        
        self.assertEqual(client, mock_backend)
        mock_backend.connect.assert_called_once()
        self.assertIn(1, self.manager.backends)

    @patch("telegram_checker.telegram_client.BackendFactory.create_backend")
    @patch("telegram_checker.backend.tdlib_binding.core.TDLibClient")
    async def test_get_client_tdlib_proxy(self, mock_tdlib_cls, mock_factory):
        import telegram_checker.telegram_client as tc
        tc.ACTIVE_ENGINE = "tdlib"
        
        mock_tdlib_inst = AsyncMock()
        mock_tdlib_cls.return_value = mock_tdlib_inst
        
        mock_backend = AsyncMock()
        mock_backend.is_user_authorized.return_value = True
        mock_factory.return_value = mock_backend
        
        proxy = (socks.SOCKS5, "1.1.1.1", 1080, True, "user", "pass")
        client = await self.manager.get_client(self.account_tdlib, proxy=proxy)
        
        self.assertEqual(client, mock_backend)
        
        # Verify proxy adapter sent addProxy
        calls = mock_tdlib_inst.send.call_args_list
        add_proxy_call = None
        for call in calls:
            if call[0][0].get("@type") == "addProxy":
                add_proxy_call = call[0][0]
                break
                
        self.assertIsNotNone(add_proxy_call)
        self.assertEqual(add_proxy_call["server"], "1.1.1.1")
        self.assertEqual(add_proxy_call["type"]["@type"], "proxyTypeSocks5")
        
        # Proxy clients are not cached
        self.assertNotIn(2, self.manager.backends)

if __name__ == "__main__":
    unittest.main()
