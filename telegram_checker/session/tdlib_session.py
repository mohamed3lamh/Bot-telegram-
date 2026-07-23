import os
import shutil
import database as db
from .base import SessionStorage
from typing import Any, Optional

SESSIONS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "tdlib_sessions"))

class TDLibSessionStorage(SessionStorage):
    """
    تدير جلسات TDLib (File-based Directory).
    تستخدم database.py للاحتفاظ بسجل الحساب ولكن بوضع "tdlib_managed" 
    في حقل string_session.
    """
    def __init__(self):
        if not os.path.exists(SESSIONS_DIR):
            os.makedirs(SESSIONS_DIR, exist_ok=True)

    def get_session_path(self, phone: str) -> str:
        # إزالة علامة + لتجنب مشاكل المسارات في بعض الأنظمة
        safe_phone = phone.replace("+", "")
        return os.path.join(SESSIONS_DIR, f"session_{safe_phone}")

    async def create_session(self, phone: str, api_id: int, api_hash: str, session_data: Any = None) -> str:
        # مسار الجلسة
        session_path = self.get_session_path(phone)
        if not os.path.exists(session_path):
            os.makedirs(session_path, exist_ok=True)
            
        # حفظ الحساب في قاعدة البيانات ليتوافق مع النظام القديم، لكن نضع "tdlib_managed"
        await db.save_telegram_account(phone, api_id, api_hash, "tdlib_managed")
        return session_path

    async def load_session(self, phone: str) -> Optional[str]:
        # نتحقق أولاً من قاعدة البيانات للتأكد أن الحساب مفعل وموجود
        query = "SELECT string_session FROM telegram_accounts WHERE phone = $1"
        res = await db.db_execute(query, [phone], fetch="one")
        
        if res and res[0] == "tdlib_managed":
            session_path = self.get_session_path(phone)
            # يجب أن نتأكد من وجود قاعدة بيانات TDLib في المسار
            if os.path.exists(session_path):
                return session_path
        return None

    async def delete_session(self, phone: str):
        # 1. نحذف من الداتا بيس
        query = "DELETE FROM telegram_accounts WHERE phone = $1"
        await db.db_execute(query, [phone])
        
        # 2. نحذف المجلد من القرص
        session_path = self.get_session_path(phone)
        if os.path.exists(session_path):
            shutil.rmtree(session_path, ignore_errors=True)

    async def session_exists(self, phone: str) -> bool:
        path = await self.load_session(phone)
        if path and os.path.exists(path):
            return True
        return False
