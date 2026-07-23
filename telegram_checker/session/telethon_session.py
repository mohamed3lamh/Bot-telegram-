import database as db
from .base import SessionStorage
from typing import Any, Optional

class TelethonSessionStorage(SessionStorage):
    """
    تدير جلسات Telethon (StringSession) وتحفظها في قاعدة البيانات القديمة.
    تحافظ على التوافق الكامل مع الكود القديم.
    """
    async def create_session(self, phone: str, api_id: int, api_hash: str, session_data: Any = None) -> Any:
        if not session_data or not isinstance(session_data, str):
            raise ValueError("Telethon Session requires a StringSession (str) data.")
        
        # حفظ في قاعدة البيانات القديمة بدون تعديل هيكلها
        await db.save_telegram_account(phone, api_id, api_hash, session_data)
        return session_data

    async def load_session(self, phone: str) -> Optional[str]:
        # استعلام مباشر لقراءة الجلسة
        query = "SELECT string_session FROM telegram_accounts WHERE phone = $1"
        res = await db.db_execute(query, [phone], fetch="one")
        if res and res[0] != "tdlib_managed":
            return res[0]
        return None

    async def delete_session(self, phone: str):
        # حذف الجلسة من قاعدة البيانات
        query = "DELETE FROM telegram_accounts WHERE phone = $1"
        await db.db_execute(query, [phone])

    async def session_exists(self, phone: str) -> bool:
        session = await self.load_session(phone)
        return bool(session)

    def get_session_path(self, phone: str) -> str:
        return f"DATABASE(string_session for {phone})"
