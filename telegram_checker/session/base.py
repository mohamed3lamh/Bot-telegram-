from abc import ABC, abstractmethod
from typing import Any, Optional

class SessionStorage(ABC):
    @abstractmethod
    async def create_session(self, phone: str, api_id: int, api_hash: str, session_data: Any = None) -> Any:
        """يصنع أو يحفظ الجلسة في قاعدة البيانات أو القرص"""
        pass

    @abstractmethod
    async def load_session(self, phone: str) -> Optional[Any]:
        """يسترجع بيانات الجلسة بناءً على رقم الهاتف"""
        pass

    @abstractmethod
    async def delete_session(self, phone: str):
        """يحذف الجلسة (من قاعدة البيانات أو من الملفات)"""
        pass

    @abstractmethod
    async def session_exists(self, phone: str) -> bool:
        """يتحقق مما إذا كانت الجلسة موجودة وصالحة"""
        pass

    @abstractmethod
    def get_session_path(self, phone: str) -> str:
        """يعيد مسار الملف أو النص الخاص بالجلسة (لأغراض اللوج أو التحقق)"""
        pass
