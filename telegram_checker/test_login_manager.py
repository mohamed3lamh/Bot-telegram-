import asyncio
import unittest
from unittest.mock import patch, AsyncMock, MagicMock
import os

from telegram_checker.login_manager import login_manager
from telegram_checker.backend.errors import (
    BackendSessionPasswordNeededError, BackendCodeExpiredError, BackendCodeInvalidError, BackendFloodWaitError
)

class TestLoginManager(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.phone = "+1234567890"
        self.api_id = 12345
        self.api_hash = "abcde"
        login_manager.pending.clear()

    @patch("telegram_checker.login_manager.BackendFactory.create_backend")
    @patch("telegram_checker.login_manager.ACTIVE_ENGINE", "telethon")
    @patch("telethon.TelegramClient")
    async def test_full_success_no_2fa(self, mock_client_cls, mock_factory):
        mock_backend = AsyncMock()
        mock_backend.send_code_request.return_value = {"phone_code_hash": "hash123", "type": "sms"}
        mock_backend.get_me.return_value = {"id": 111, "first_name": "Test"}
        
        # Fake Telethon Client session
        mock_client = MagicMock()
        mock_client.session.save.return_value = "fake_session_string"
        mock_backend.client = mock_client
        mock_factory.return_value = mock_backend
        
        # 1. Send Code
        res1 = await login_manager.send_code(self.phone, self.api_id, self.api_hash)
        self.assertTrue(res1)
        self.assertIn(self.phone, login_manager.pending)
        
        # 2. Verify Code
        res2 = await login_manager.verify_code(self.phone, "00000")
        self.assertEqual(res2["status"], "SUCCESS")
        self.assertEqual(res2["session"], "fake_session_string")
        self.assertNotIn(self.phone, login_manager.pending)
        
        mock_backend.sign_in_code.assert_called_with(phone=self.phone, code="00000", phone_code_hash="hash123")

    @patch("telegram_checker.login_manager.BackendFactory.create_backend")
    @patch("telegram_checker.login_manager.ACTIVE_ENGINE", "tdlib")
    @patch("telegram_checker.backend.tdlib_binding.core.TDLibClient")
    async def test_full_success_with_2fa(self, mock_tdlib_cls, mock_factory):
        mock_backend = AsyncMock()
        mock_backend.send_code_request.return_value = {"phone_code_hash": "tdlib_managed", "type": "tdlib"}
        mock_backend.sign_in_code.side_effect = BackendSessionPasswordNeededError()
        mock_backend.get_me.return_value = {"id": 222, "first_name": "TDTest"}
        mock_factory.return_value = mock_backend
        
        # 1. Send Code
        await login_manager.send_code(self.phone, self.api_id, self.api_hash)
        
        # 2. Verify Code (Needs Password)
        res2 = await login_manager.verify_code(self.phone, "00000")
        self.assertEqual(res2["status"], "PASSWORD_REQUIRED")
        self.assertIn(self.phone, login_manager.pending)
        
        # 3. Verify Password
        res3 = await login_manager.verify_password(self.phone, "mypass")
        self.assertEqual(res3["status"], "SUCCESS")
        self.assertEqual(res3["session"], "tdlib_managed")
        self.assertNotIn(self.phone, login_manager.pending)

    @patch("telegram_checker.login_manager.BackendFactory.create_backend")
    @patch("telegram_checker.login_manager.ACTIVE_ENGINE", "telethon")
    @patch("telethon.TelegramClient")
    async def test_errors_code_invalid(self, mock_client_cls, mock_factory):
        mock_backend = AsyncMock()
        mock_backend.send_code_request.return_value = {"phone_code_hash": "hash123", "type": "sms"}
        mock_backend.sign_in_code.side_effect = BackendCodeInvalidError()
        mock_factory.return_value = mock_backend
        
        await login_manager.send_code(self.phone, self.api_id, self.api_hash)
        
        with self.assertRaisesRegex(Exception, "الكود غير صحيح"):
            await login_manager.verify_code(self.phone, "wrong")

if __name__ == "__main__":
    unittest.main()
