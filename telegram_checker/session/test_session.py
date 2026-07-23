import unittest
from unittest.mock import AsyncMock, patch, MagicMock
import asyncio
import os

from telegram_checker.session.telethon_session import TelethonSessionStorage
from telegram_checker.session.tdlib_session import TDLibSessionStorage

class TestSessionStorage(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.phone = "+1234567890"
        self.api_id = 12345
        self.api_hash = "abcdef"

    @patch("telegram_checker.session.telethon_session.db.save_telegram_account", new_callable=AsyncMock)
    @patch("telegram_checker.session.telethon_session.db.db_execute", new_callable=AsyncMock)
    async def test_telethon_session_lifecycle(self, mock_db_execute, mock_save):
        storage = TelethonSessionStorage()
        session_str = "1ApWapzMB..."
        
        # 1. Create
        res = await storage.create_session(self.phone, self.api_id, self.api_hash, session_str)
        self.assertEqual(res, session_str)
        mock_save.assert_called_once_with(self.phone, self.api_id, self.api_hash, session_str)
        
        # 2. Load
        mock_db_execute.return_value = (session_str,)
        loaded = await storage.load_session(self.phone)
        self.assertEqual(loaded, session_str)
        
        # 3. Exists
        exists = await storage.session_exists(self.phone)
        self.assertTrue(exists)
        
        # 4. Delete
        await storage.delete_session(self.phone)
        mock_db_execute.assert_called_with("DELETE FROM telegram_accounts WHERE phone = $1", [self.phone])

    @patch("telegram_checker.session.tdlib_session.db.save_telegram_account", new_callable=AsyncMock)
    @patch("telegram_checker.session.tdlib_session.db.db_execute", new_callable=AsyncMock)
    @patch("telegram_checker.session.tdlib_session.shutil.rmtree")
    @patch("telegram_checker.session.tdlib_session.os.path.exists")
    @patch("telegram_checker.session.tdlib_session.os.makedirs")
    async def test_tdlib_session_lifecycle(self, mock_makedirs, mock_exists, mock_rmtree, mock_db_execute, mock_save):
        storage = TDLibSessionStorage()
        
        # 1. Create
        mock_exists.return_value = False
        path = await storage.create_session(self.phone, self.api_id, self.api_hash)
        self.assertTrue("session_1234567890" in path)
        mock_save.assert_called_once_with(self.phone, self.api_id, self.api_hash, "tdlib_managed")
        
        # 2. Load
        mock_db_execute.return_value = ("tdlib_managed",)
        mock_exists.return_value = True  # Directory exists
        loaded = await storage.load_session(self.phone)
        self.assertEqual(loaded, path)
        
        # 3. Exists
        exists = await storage.session_exists(self.phone)
        self.assertTrue(exists)
        
        # 4. Delete
        await storage.delete_session(self.phone)
        mock_db_execute.assert_called_with("DELETE FROM telegram_accounts WHERE phone = $1", [self.phone])
        mock_rmtree.assert_called_once_with(path, ignore_errors=True)

if __name__ == "__main__":
    unittest.main()
