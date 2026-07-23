import asyncio
from typing import Dict, Any, List, Optional
from .base import TelegramBackend
from .errors import *
from .tdlib_binding.core import TDLibClient

class TDLibBackend(TelegramBackend):
    def __init__(self, client: TDLibClient):
        self.client = client
        self._is_connected = False

    async def connect(self):
        if not self._is_connected:
            self._is_connected = True
            # دورة التحديثات تدار من الخارج أو من الـ Client نفسه

    async def disconnect(self):
        if self.client:
            self.client.stop()
        self._is_connected = False

    def is_connected(self) -> bool:
        return self._is_connected

    async def is_user_authorized(self) -> bool:
        res = await self.client.send({"@type": "getAuthorizationState"})
        state = res.get("@type", "")
        return state == "authorizationStateReady"

    async def get_me(self) -> Dict[str, Any]:
        res = await self.client.send({"@type": "getMe"})
        if res.get("@type") == "error":
            raise BackendError(res.get("message"))
        return {
            "id": res.get("id"),
            "first_name": res.get("first_name", ""),
            "username": res.get("usernames", {}).get("editable_username", "") if res.get("usernames") else ""
        }

    # ================= المصادقة (Auth) =================
    async def send_code_request(self, phone: str, force_sms: bool = False) -> Dict[str, Any]:
        res = await self.client.send({
            "@type": "setAuthenticationPhoneNumber",
            "phone_number": phone,
            "settings": {
                "@type": "phoneNumberAuthenticationSettings",
                "allow_flash_call": False,
                "is_current_phone_number": False,
                "allow_sms_retriever_api": False
            }
        })
        if res.get("@type") == "error":
            code = res.get("code")
            msg = res.get("message", "")
            if code == 429:
                raise BackendFloodWaitError(60) 
            raise BackendError(msg)
        return {"phone_code_hash": "tdlib_managed", "type": "tdlib"}

    async def resend_code_request(self, phone: str, phone_code_hash: str) -> Dict[str, Any]:
        res = await self.client.send({"@type": "resendAuthenticationCode"})
        if res.get("@type") == "error":
            raise BackendError(res.get("message"))
        return {"phone_code_hash": "tdlib_managed", "type": "tdlib"}

    async def sign_in_code(self, phone: str, code: str, phone_code_hash: str) -> Dict[str, Any]:
        res = await self.client.send({
            "@type": "checkAuthenticationCode",
            "code": code
        })
        if res.get("@type") == "error":
            msg = res.get("message", "")
            if "PHONE_CODE_EXPIRED" in msg:
                raise BackendCodeExpiredError()
            if "PHONE_CODE_INVALID" in msg:
                raise BackendCodeInvalidError()
            if "SESSION_PASSWORD_NEEDED" in msg:
                raise BackendSessionPasswordNeededError()
            raise BackendError(msg)
        return {"status": "SUCCESS"}

    async def sign_in_password(self, password: str) -> Dict[str, Any]:
        res = await self.client.send({
            "@type": "checkAuthenticationPassword",
            "password": password
        })
        if res.get("@type") == "error":
            raise BackendError(res.get("message"))
        return {"status": "SUCCESS"}

    async def cancel_code(self, phone: str, phone_code_hash: str):
        pass # يُدار عبر إغلاق الجلسة

    # ================= الفحص (Checking) =================
    async def import_contacts(self, phones: List[str]) -> Dict[str, Any]:
        contacts = []
        for p in phones:
            contacts.append({
                "@type": "contact",
                "phone_number": p,
                "first_name": "TempCheck",
                "last_name": "",
                "user_id": 0
            })
        
        res = await self.client.send({
            "@type": "importContacts",
            "contacts": contacts
        })
        
        if res.get("@type") == "error":
            msg = res.get("message", "")
            if res.get("code") == 429:
                raise BackendFloodWaitError(60)
            if "UNAUTHORIZED" in msg:
                raise BackendSessionUnauthorizedError()
            raise BackendError(msg)
            
        user_ids = res.get("user_ids", [])
        return {
            "users": [{"id": uid} for uid in user_ids if uid > 0],
            "imported": [{"user_id": uid} for uid in user_ids if uid > 0]
        }

    async def delete_contacts(self, user_ids: List[int]):
        await self.client.send({
            "@type": "removeContacts",
            "user_ids": user_ids
        })

    async def resolve_phone(self, phone: str) -> Dict[str, Any]:
        res = await self.client.send({
            "@type": "searchUserByPhoneNumber",
            "phone_number": phone
        })
        if res.get("@type") == "error":
            code = res.get("code")
            msg = res.get("message", "")
            if code == 429:
                raise BackendFloodWaitError(60)
            if code == 404 or "NOT_FOUND" in msg:
                raise BackendPhoneUnoccupiedError()
            if code == 403 or "PRIVACY" in msg:
                raise BackendPrivacyError()
            if "BANNED" in msg:
                raise BackendPhoneBannedError()
            if "UNAUTHORIZED" in msg:
                raise BackendSessionUnauthorizedError()
            raise BackendError(msg)
            
        user_id = res.get("id")
        if user_id:
            return {"users": [{"id": user_id}]}
        return {"users": []}

    async def check_layer3_send_code(self, phone: str, api_id: int, api_hash: str) -> Dict[str, Any]:
        """
        [قيد الانتظار]
        سيتم تنفيذ هذه الواجهة باستخدام Ephemeral TDLib Client بعد انتهاء تقييم الاختبارات.
        """
        raise NotImplementedError("Layer 3 is pending validation for TDLibBackend.")

    # ================= الرسائل =================
    async def send_message(self, username: str, text: str):
        search_res = await self.client.send({
            "@type": "searchPublicChat",
            "username": username.replace("@", "")
        })
        if search_res.get("@type") == "error":
            raise BackendError(search_res.get("message"))
            
        chat_id = search_res.get("id")
        res = await self.client.send({
            "@type": "sendMessage",
            "chat_id": chat_id,
            "input_message_content": {
                "@type": "inputMessageText",
                "text": {"@type": "formattedText", "text": text}
            }
        })
        if res.get("@type") == "error":
            raise BackendError(res.get("message"))

    async def get_messages(self, username: str, limit: int) -> List[Dict[str, Any]]:
        search_res = await self.client.send({
            "@type": "searchPublicChat",
            "username": username.replace("@", "")
        })
        if search_res.get("@type") == "error":
            raise BackendError(search_res.get("message"))
            
        chat_id = search_res.get("id")
        res = await self.client.send({
            "@type": "getChatHistory",
            "chat_id": chat_id,
            "from_message_id": 0,
            "offset": 0,
            "limit": limit,
            "only_local": False
        })
        
        if res.get("@type") == "error":
            raise BackendError(res.get("message"))
            
        messages = res.get("messages", [])
        formatted = []
        for msg in messages:
            content = msg.get("content", {})
            text = content.get("text", {}).get("text", "")
            formatted.append({
                "out": msg.get("is_outgoing", False),
                "date": msg.get("date", 0),
                "text": text
            })
        return formatted

    async def switch_dc(self, new_dc: int):
        pass # TDLib يدير الخوادم تلقائياً
