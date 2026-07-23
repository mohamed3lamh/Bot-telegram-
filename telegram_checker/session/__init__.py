from .base import SessionStorage
from .telethon_session import TelethonSessionStorage
from .tdlib_session import TDLibSessionStorage
import os

ACTIVE_ENGINE = os.getenv("TELEGRAM_ENGINE", "telethon").lower()

def get_session_storage() -> SessionStorage:
    """مصنع لإرجاع مخزن الجلسات المناسب بناءً على المحرك"""
    if ACTIVE_ENGINE == "tdlib":
        return TDLibSessionStorage()
    return TelethonSessionStorage()

__all__ = ["SessionStorage", "TelethonSessionStorage", "TDLibSessionStorage", "get_session_storage"]
