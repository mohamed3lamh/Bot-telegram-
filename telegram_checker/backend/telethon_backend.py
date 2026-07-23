import asyncio
from typing import Dict, Any, List, Optional
from telethon import TelegramClient, functions, types, errors
from .base import TelegramBackend
from .errors import *

class TelethonBackend(TelegramBackend):
    def __init__(self, client: TelegramClient):
        self.client = client

    async def connect(self):
        if not self.client.is_connected():
            await self.client.connect()

    async def disconnect(self):
        await self.client.disconnect()

    def is_connected(self) -> bool:
        return self.client.is_connected()

    async def is_user_authorized(self) -> bool:
        return await self.client.is_user_authorized()

    async def get_me(self) -> Dict[str, Any]:
        me = await self.client.get_me()
        return {
            "id": me.id,
            "first_name": me.first_name,
            "username": me.username
        }

    # ================= المصادقة (Auth) =================
    async def send_code_request(self, phone: str, force_sms: bool = False) -> Dict[str, Any]:
        try:
            res = await self.client.send_code_request(phone, force_sms=force_sms)
            return {"phone_code_hash": res.phone_code_hash, "type": type(res.type).__name__}
        except errors.FloodWaitError as e:
            raise BackendFloodWaitError(e.seconds)
        except errors.ApiIdInvalidError:
            raise BackendApiIdInvalidError()
        except errors.PhoneNumberInvalidError:
            raise BackendPhoneInvalidError()

    async def resend_code_request(self, phone: str, phone_code_hash: str) -> Dict[str, Any]:
        try:
            res = await self.client(functions.auth.ResendCodeRequest(
                phone_number=phone,
                phone_code_hash=phone_code_hash
            ))
            return {"phone_code_hash": res.phone_code_hash, "type": type(res.type).__name__}
        except Exception as e:
            raise BackendError(str(e))

    async def sign_in_code(self, phone: str, code: str, phone_code_hash: str) -> Dict[str, Any]:
        try:
            await self.client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
            return {"status": "SUCCESS"}
        except errors.SessionPasswordNeededError:
            raise BackendSessionPasswordNeededError()
        except errors.PhoneCodeExpiredError:
            raise BackendCodeExpiredError()
        except errors.PhoneCodeInvalidError:
            raise BackendCodeInvalidError()
        except errors.FloodWaitError as e:
            raise BackendFloodWaitError(e.seconds)

    async def sign_in_password(self, password: str) -> Dict[str, Any]:
        try:
            await self.client.sign_in(password=password)
            return {"status": "SUCCESS"}
        except errors.FloodWaitError as e:
            raise BackendFloodWaitError(e.seconds)
        except Exception as e:
            raise BackendError(str(e))

    async def cancel_code(self, phone: str, phone_code_hash: str):
        try:
            await self.client(functions.auth.CancelCodeRequest(
                phone_number=phone,
                phone_code_hash=phone_code_hash
            ))
        except Exception:
            pass

    # ================= الفحص (Checking) =================
    async def import_contacts(self, phones: List[str]) -> Dict[str, Any]:
        contacts = [types.InputPhoneContact(client_id=0, phone=p, first_name="TempCheck", last_name="") for p in phones]
        try:
            res = await self.client(functions.contacts.ImportContactsRequest(contacts=contacts))
            return {
                "users": [{"id": u.id} for u in getattr(res, "users", [])],
                "imported": [{"user_id": i.user_id} for i in getattr(res, "imported", [])]
            }
        except errors.PhoneMigrateError as e:
            raise BackendPhoneMigrateError(e.new_dc)
        except errors.FloodWaitError as e:
            raise BackendFloodWaitError(e.seconds)
        except Exception as e:
            error_str = str(e).upper()
            if "BANNED" in error_str or "AUTH_KEY_UNREGISTERED" in error_str:
                raise BackendSessionUnauthorizedError()
            raise BackendError(str(e))

    async def delete_contacts(self, user_ids: List[int]):
        try:
            await self.client(functions.contacts.DeleteContactsRequest(id=user_ids))
        except Exception:
            pass

    async def resolve_phone(self, phone: str) -> Dict[str, Any]:
        try:
            res = await self.client(functions.contacts.ResolvePhoneRequest(phone=phone))
            if res.users:
                return {"users": [{"id": res.users[0].id}]}
            return {"users": []}
        except errors.UserPrivacyRestrictedError:
            raise BackendPrivacyError()
        except errors.PhoneNumberUnoccupiedError:
            raise BackendPhoneUnoccupiedError()
        except errors.PhoneNumberBannedError:
            raise BackendPhoneBannedError()
        except errors.PhoneNumberInvalidError:
            raise BackendPhoneInvalidError()
        except errors.FloodWaitError as e:
            raise BackendFloodWaitError(e.seconds)
        except Exception as e:
            error_str = str(e).upper()
            error_type = type(e).__name__.upper()
            PRIVACY = ["PRIVACY", "PRIVACY_RESTRICTED", "USERPRIVACYRESTRICTED"]
            NO_SESSION = ["UNOCCUPIED", "NO USER", "NOT FOUND", "NOT_FOUND", "NO_PHONE_ASSOCIATED"]
            BANNED = ["BANNED", "PHONE_NUMBER_BANNED"]
            if any(kw in error_str or kw in error_type for kw in PRIVACY):
                raise BackendPrivacyError()
            elif any(kw in error_str or kw in error_type for kw in NO_SESSION):
                raise BackendPhoneUnoccupiedError()
            elif any(kw in error_str or kw in error_type for kw in BANNED):
                raise BackendPhoneBannedError()
            elif "AUTH_KEY" in error_str:
                raise BackendSessionUnauthorizedError()
            raise BackendError(str(e))

    async def check_layer3_send_code(self, phone: str, api_id: int, api_hash: str) -> Dict[str, Any]:
        try:
            res = await self.client(functions.auth.SendCodeRequest(
                phone_number=phone,
                api_id=api_id,
                api_hash=api_hash,
                settings=types.CodeSettings(allow_flashcall=False, current_number=True, allow_app_hash=True)
            ))
            return {"status": "HAS_SESSION", "phone_code_hash": res.phone_code_hash}
        except errors.PhoneNumberUnoccupiedError:
            raise BackendPhoneUnoccupiedError()
        except errors.PhoneNumberBannedError:
            raise BackendPhoneBannedError()
        except errors.PhoneNumberInvalidError:
            raise BackendPhoneInvalidError()
        except errors.SessionPasswordNeededError:
            raise BackendSessionPasswordNeededError()
        except errors.PhoneMigrateError as e:
            raise BackendPhoneMigrateError(e.new_dc)
        except errors.FloodWaitError as e:
            raise BackendFloodWaitError(e.seconds)
        except Exception as e:
            error_str = str(e).upper()
            if any(kw in error_str for kw in ["UNOCCUPIED", "NO USER", "NOT FOUND", "NOT_FOUND"]):
                raise BackendPhoneUnoccupiedError()
            if "BANNED" in error_str:
                raise BackendPhoneBannedError()
            if "AUTH_KEY" in error_str:
                raise BackendSessionUnauthorizedError()
            raise BackendError(str(e))

    # ================= الرسائل =================
    async def send_message(self, username: str, text: str):
        try:
            await self.client.send_message(username, text)
        except Exception as e:
            raise BackendError(str(e))

    async def get_messages(self, username: str, limit: int) -> List[Dict[str, Any]]:
        try:
            messages = await self.client.get_messages(username, limit=limit)
            return [
                {
                    "out": msg.out,
                    "date": msg.date,
                    "text": msg.text
                }
                for msg in messages
            ]
        except Exception as e:
            raise BackendError(str(e))

    async def switch_dc(self, new_dc: int):
        await self.client._switch_dc(new_dc)
