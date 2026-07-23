import unittest
from unittest.mock import AsyncMock, MagicMock
import asyncio

from telethon import errors as telethon_errors
from telethon import types as telethon_types

from telegram_checker.backend.errors import *
from telegram_checker.backend.telethon_backend import TelethonBackend
from telegram_checker.backend.tdlib_backend import TDLibBackend

class TestBackendCompatibility(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.telethon_mock = AsyncMock()
        self.telethon_mock.__call__ = AsyncMock()
        self.telethon_mock.is_connected = MagicMock(return_value=False)
        self.telethon_backend = TelethonBackend(self.telethon_mock)
        
        self.tdlib_mock = AsyncMock()
        self.tdlib_backend = TDLibBackend(self.tdlib_mock)

    async def compare_backends(self, func_name, telethon_setup, tdlib_setup, *args, expected_exception=None, expected_result=None):
        telethon_setup()
        tdlib_setup()
        
        func_telethon = getattr(self.telethon_backend, func_name)
        func_tdlib = getattr(self.tdlib_backend, func_name)
        
        if expected_exception:
            with self.assertRaises(expected_exception) as cm_tel:
                await func_telethon(*args)
            with self.assertRaises(expected_exception) as cm_tdl:
                await func_tdlib(*args)
                
            if expected_exception == BackendFloodWaitError:
                self.assertEqual(cm_tel.exception.seconds, cm_tdl.exception.seconds)
                
        elif expected_result is not None:
            res_tel = await func_telethon(*args)
            res_tdl = await func_tdlib(*args)
            self.assertEqual(res_tel, expected_result)
            self.assertEqual(res_tel, res_tdl)
        else:
            # Function returns None, just ensure they run without raising
            await func_telethon(*args)
            await func_tdlib(*args)

    # ================= Stage 1: Lifecycle =================
    async def test_lifecycle_connect_disconnect(self):
        # 1. Connect
        await self.telethon_backend.connect()
        self.telethon_mock.connect.assert_called_once()
        self.telethon_mock.is_connected = MagicMock(return_value=True)
        self.assertTrue(self.telethon_backend.is_connected())
        
        await self.tdlib_backend.connect()
        self.assertTrue(self.tdlib_backend.is_connected())
        
        # 2. Disconnect
        await self.telethon_backend.disconnect()
        self.telethon_mock.disconnect.assert_called_once()
        self.telethon_mock.is_connected = MagicMock(return_value=False)
        self.assertFalse(self.telethon_backend.is_connected())
        
        await self.tdlib_backend.disconnect()
        self.tdlib_mock.stop.assert_called_once()
        self.assertFalse(self.tdlib_backend.is_connected())

    async def test_lifecycle_is_user_authorized(self):
        def tel_setup():
            self.telethon_mock.is_user_authorized.return_value = True
        def tdl_setup():
            self.tdlib_mock.send.return_value = {"@type": "authorizationStateReady"}
        await self.compare_backends("is_user_authorized", tel_setup, tdl_setup, expected_result=True)

    async def test_lifecycle_get_me(self):
        def tel_setup():
            mock_me = MagicMock()
            mock_me.id = 111
            mock_me.first_name = "Test"
            mock_me.username = "tester"
            self.telethon_mock.get_me = AsyncMock(return_value=mock_me)
        def tdl_setup():
            self.tdlib_mock.send.return_value = {
                "@type": "user",
                "id": 111,
                "first_name": "Test",
                "usernames": {"editable_username": "tester"}
            }
        expected = {"id": 111, "first_name": "Test", "username": "tester"}
        await self.compare_backends("get_me", tel_setup, tdl_setup, expected_result=expected)

    async def test_lifecycle_switch_dc(self):
        def tel_setup():
            self.telethon_mock._switch_dc = AsyncMock()
        def tdl_setup():
            pass
        await self.compare_backends("switch_dc", tel_setup, tdl_setup, 2)
        self.telethon_mock._switch_dc.assert_called_once_with(2)

    # ================= Stage 3 (Existing) =================
    async def test_resolve_phone_success(self):
        def tel_setup():
            mock_res = MagicMock()
            mock_res.users = [MagicMock(id=123456)]
            self.telethon_mock.side_effect = [mock_res]
        def tdl_setup():
            self.tdlib_mock.send.return_value = {"@type": "user", "id": 123456}
        await self.compare_backends("resolve_phone", tel_setup, tdl_setup, "+123", expected_result={"users": [{"id": 123456}]})

    async def test_resolve_phone_privacy(self):
        def tel_setup():
            self.telethon_mock.side_effect = telethon_errors.UserPrivacyRestrictedError(request=None)
        def tdl_setup():
            self.tdlib_mock.send.return_value = {"@type": "error", "code": 403, "message": "USER_PRIVACY_RESTRICTED"}
        await self.compare_backends("resolve_phone", tel_setup, tdl_setup, "+123", expected_exception=BackendPrivacyError)

    async def test_resolve_phone_banned(self):
        def tel_setup():
            self.telethon_mock.side_effect = telethon_errors.PhoneNumberBannedError(request=None)
        def tdl_setup():
            self.tdlib_mock.send.return_value = {"@type": "error", "code": 400, "message": "PHONE_NUMBER_BANNED"}
        await self.compare_backends("resolve_phone", tel_setup, tdl_setup, "+123", expected_exception=BackendPhoneBannedError)

    async def test_resolve_phone_unoccupied(self):
        def tel_setup():
            self.telethon_mock.side_effect = telethon_errors.PhoneNumberUnoccupiedError(request=None)
        def tdl_setup():
            self.tdlib_mock.send.return_value = {"@type": "error", "code": 404, "message": "NOT_FOUND"}
        await self.compare_backends("resolve_phone", tel_setup, tdl_setup, "+123", expected_exception=BackendPhoneUnoccupiedError)

    async def test_resolve_phone_floodwait(self):
        def tel_setup():
            self.telethon_mock.side_effect = telethon_errors.FloodWaitError(request=None, capture=60)
        def tdl_setup():
            self.tdlib_mock.send.return_value = {"@type": "error", "code": 429, "message": "Too Many Requests: retry after 60"}
        await self.compare_backends("resolve_phone", tel_setup, tdl_setup, "+123", expected_exception=BackendFloodWaitError)

    async def test_import_contacts_success(self):
        def tel_setup():
            mock_res = MagicMock()
            user_mock = MagicMock()
            user_mock.id = 9876
            imported_mock = MagicMock()
            imported_mock.user_id = 9876
            mock_res.users = [user_mock]
            mock_res.imported = [imported_mock]
            self.telethon_mock.side_effect = [mock_res]
        def tdl_setup():
            self.tdlib_mock.send.return_value = {"@type": "importedContacts", "user_ids": [9876]}
        await self.compare_backends("import_contacts", tel_setup, tdl_setup, ["+123"], 
                                    expected_result={"users": [{"id": 9876}], "imported": [{"user_id": 9876}]})

    async def test_import_contacts_unauthorized(self):
        def tel_setup():
            self.telethon_mock.side_effect = Exception("AUTH_KEY_UNREGISTERED")
        def tdl_setup():
            self.tdlib_mock.send.return_value = {"@type": "error", "code": 401, "message": "UNAUTHORIZED"}
        await self.compare_backends("import_contacts", tel_setup, tdl_setup, ["+123"], expected_exception=BackendSessionUnauthorizedError)

    async def test_sign_in_code_expired(self):
        def tel_setup():
            self.telethon_mock.sign_in.side_effect = telethon_errors.PhoneCodeExpiredError(request=None)
        def tdl_setup():
            self.tdlib_mock.send.return_value = {"@type": "error", "code": 400, "message": "PHONE_CODE_EXPIRED"}
        await self.compare_backends("sign_in_code", tel_setup, tdl_setup, "+123", "00000", "hash", expected_exception=BackendCodeExpiredError)

if __name__ == "__main__":
    unittest.main()

    # ================= Stage 2: Auth =================
    async def test_auth_send_code_success(self):
        # Telethon
        mock_res = MagicMock()
        mock_res.phone_code_hash = "hash123"
        mock_res.type = type("App", (object,), {})()
        self.telethon_mock.send_code_request = AsyncMock(return_value=mock_res)
        res_tel = await self.telethon_backend.send_code_request("+123", False)
        self.assertEqual(res_tel["phone_code_hash"], "hash123")
        
        # TDLib
        self.tdlib_mock.send.return_value = {"@type": "ok"}
        res_tdl = await self.tdlib_backend.send_code_request("+123", False)
        self.assertEqual(res_tdl["phone_code_hash"], "tdlib_managed")

    async def test_auth_send_code_floodwait(self):
        def tel_setup():
            self.telethon_mock.send_code_request = AsyncMock(side_effect=telethon_errors.FloodWaitError(request=None, capture=60))
        def tdl_setup():
            self.tdlib_mock.send.return_value = {"@type": "error", "code": 429, "message": "Too Many Requests: retry after 60"}
        await self.compare_backends("send_code_request", tel_setup, tdl_setup, "+123", expected_exception=BackendFloodWaitError)

    async def test_auth_resend_code(self):
        # Telethon
        mock_res = MagicMock()
        mock_res.phone_code_hash = "hash_new"
        mock_res.type = type("App", (object,), {})()
        self.telethon_mock.side_effect = [mock_res]
        res_tel = await self.telethon_backend.resend_code_request("+123", "hash_old")
        self.assertEqual(res_tel["phone_code_hash"], "hash_new")
        
        # TDLib
        self.tdlib_mock.send.return_value = {"@type": "ok"}
        res_tdl = await self.tdlib_backend.resend_code_request("+123", "tdlib_managed")
        self.assertEqual(res_tdl["phone_code_hash"], "tdlib_managed")

    async def test_auth_sign_in_success(self):
        def tel_setup():
            self.telethon_mock.sign_in = AsyncMock()
        def tdl_setup():
            self.tdlib_mock.send.return_value = {"@type": "ok"}
        await self.compare_backends("sign_in_code", tel_setup, tdl_setup, "+123", "12345", "hash", expected_result={"status": "SUCCESS"})

    async def test_auth_sign_in_password_needed(self):
        def tel_setup():
            self.telethon_mock.sign_in = AsyncMock(side_effect=telethon_errors.SessionPasswordNeededError(request=None))
        def tdl_setup():
            self.tdlib_mock.send.return_value = {"@type": "error", "code": 401, "message": "SESSION_PASSWORD_NEEDED"}
        await self.compare_backends("sign_in_code", tel_setup, tdl_setup, "+123", "12345", "hash", expected_exception=BackendSessionPasswordNeededError)

    async def test_auth_sign_in_invalid_code(self):
        def tel_setup():
            self.telethon_mock.sign_in = AsyncMock(side_effect=telethon_errors.PhoneCodeInvalidError(request=None))
        def tdl_setup():
            self.tdlib_mock.send.return_value = {"@type": "error", "code": 400, "message": "PHONE_CODE_INVALID"}
        await self.compare_backends("sign_in_code", tel_setup, tdl_setup, "+123", "00000", "hash", expected_exception=BackendCodeInvalidError)

    async def test_auth_sign_in_password_success(self):
        def tel_setup():
            self.telethon_mock.sign_in = AsyncMock()
        def tdl_setup():
            self.tdlib_mock.send.return_value = {"@type": "ok"}
        await self.compare_backends("sign_in_password", tel_setup, tdl_setup, "my_pass", expected_result={"status": "SUCCESS"})
