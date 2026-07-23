import os
from typing import Any
from .base import TelegramBackend
from .telethon_backend import TelethonBackend
from .tdlib_backend import TDLibBackend
from .tdlib_binding.core import TDLibClient

# يمكن تغيير هذا الإعداد لتجربة المحركات المختلفة بحرية
# الخيارات: "telethon" أو "tdlib"
ACTIVE_ENGINE = os.getenv("TELEGRAM_ENGINE", "telethon").lower()

class BackendFactory:
    @staticmethod
    def create_backend(client_instance: Any) -> TelegramBackend:
        """
        يقوم بتغليف كائن العميل (TelegramClient أو TDLibClient) 
        داخل واجهة الـ Backend الموحدة بناءً على نوع المحرك المختار.
        """
        if ACTIVE_ENGINE == "tdlib":
            if not isinstance(client_instance, TDLibClient):
                raise TypeError(f"Expected TDLibClient for TDLib engine, got {type(client_instance)}")
            return TDLibBackend(client_instance)
        else:
            # في حالتنا الحالية هو TelegramClient (Telethon)
            return TelethonBackend(client_instance)
