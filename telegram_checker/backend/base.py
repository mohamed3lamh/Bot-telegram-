from typing import Dict, Any, Optional, List
from abc import ABC, abstractmethod

class TelegramBackend(ABC):
    """
    طبقة التجريد (Abstraction Layer) للتواصل مع تيليجرام.
    أي محرك (Telethon أو TDLib) يجب أن يرث من هذه الكلاس ويطبق دوالها.
    """

    @abstractmethod
    async def connect(self):
        pass

    @abstractmethod
    async def disconnect(self):
        pass

    @abstractmethod
    def is_connected(self) -> bool:
        pass

    @abstractmethod
    async def is_user_authorized(self) -> bool:
        pass

    @abstractmethod
    async def get_me(self) -> Dict[str, Any]:
        pass

    # ================= المصادقة (Auth) =================
    @abstractmethod
    async def send_code_request(self, phone: str, force_sms: bool = False) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def resend_code_request(self, phone: str, phone_code_hash: str) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def sign_in_code(self, phone: str, code: str, phone_code_hash: str) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def sign_in_password(self, password: str) -> Dict[str, Any]:
        pass
        
    @abstractmethod
    async def cancel_code(self, phone: str, phone_code_hash: str):
        pass

    # ================= الفحص (Checking) =================
    @abstractmethod
    async def import_contacts(self, phones: List[str]) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def delete_contacts(self, user_ids: List[int]):
        pass

    @abstractmethod
    async def resolve_phone(self, phone: str) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def check_layer3_send_code(self, phone: str, api_id: int, api_hash: str) -> Dict[str, Any]:
        """
        واجهة مستقلة للطبقة الثالثة. 
        يجب أن تُرجع قاموساً بحالة الرقم أو تثير الأخطاء المجردة (BackendError).
        """
        pass

    # ================= الرسائل (Messaging) =================
    @abstractmethod
    async def send_message(self, username: str, text: str):
        pass

    @abstractmethod
    async def get_messages(self, username: str, limit: int) -> List[Dict[str, Any]]:
        pass

    # ================= عمليات أخرى =================
    @abstractmethod
    async def switch_dc(self, new_dc: int):
        pass
