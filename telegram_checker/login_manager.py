import asyncio
import os
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError, PhoneCodeExpiredError, PhoneCodeInvalidError,
    FloodWaitError, ApiIdInvalidError, PhoneNumberInvalidError,
)
from database import save_telegram_account

class LoginManager:
    def __init__(self):
        # الحسابات التي تنتظر إدخال الكود أو كلمة المرور
        self.pending = {}

    async def _cleanup_phone(self, phone):
        """إزالة أي جلسة سابقة لنفس الرقم"""
        if phone in self.pending:
            try:
                await self.pending[phone]["client"].disconnect()
            except:
                pass
            del self.pending[phone]

    async def send_code(self, phone, api_id, api_hash):
        # تنظيف أي عملية سابقة لنفس الرقم
        await self._cleanup_phone(phone)

        client = TelegramClient(StringSession(), int(api_id), api_hash)
        await client.connect()
        try:
            result = await client.send_code_request(phone)
        except FloodWaitError:
            await client.disconnect()
            raise
        except ApiIdInvalidError:
            await client.disconnect()
            raise Exception("API_ID أو API_HASH غير صحيح.")
        except PhoneNumberInvalidError:
            await client.disconnect()
            raise Exception("رقم الهاتف غير صحيح.")

        self.pending[phone] = {
            "client": client,
            "phone": phone,
            "api_id": int(api_id),
            "api_hash": api_hash,
            "phone_code_hash": result.phone_code_hash
        }
        return True

    async def verify_code(self, phone, code):
        if phone not in self.pending:
            raise Exception("لا يوجد طلب تسجيل دخول لهذا الرقم. ابدأ العملية من جديد.")
        
        data = self.pending[phone]
        client = data["client"]
        try:
            await client.sign_in(phone=phone, code=code, phone_code_hash=data["phone_code_hash"])
            return await self._finish_login(phone)
        except SessionPasswordNeededError:
            return {"status": "PASSWORD_REQUIRED"}
        except PhoneCodeInvalidError:
            raise Exception("كود التحقق غير صحيح.")
        except PhoneCodeExpiredError:
            # إعادة إرسال الكود تلقائياً
            try:
                result = await client.send_code_request(phone)
                data["phone_code_hash"] = result.phone_code_hash
                # إعلام المستخدم بأن الكود انتهت صلاحيته وتم إرسال كود جديد
                return {"status": "CODE_EXPIRED", "message": "انتهت صلاحية الكود. تم إرسال كود جديد، يرجى إدخاله."}
            except Exception as e:
                await client.disconnect()
                del self.pending[phone]
                raise Exception(f"فشل إعادة إرسال الكود: {e}")
        except FloodWaitError:
            raise
        except Exception:
            raise

    async def verify_password(self, phone, password):
        if phone not in self.pending:
            raise Exception("لا يوجد تسجيل دخول نشط لهذا الرقم.")
        
        data = self.pending[phone]
        client = data["client"]
        try:
            await client.sign_in(password=password)
            return await self._finish_login(phone)
        except FloodWaitError:
            raise
        except Exception:
            raise Exception("كلمة مرور التحقق بخطوتين غير صالحة.")

    async def _finish_login(self, phone):
        data = self.pending[phone]
        client = data["client"]
        me = await client.get_me()
        string_session = client.session.save()
        
        save_telegram_account(
            phone=phone,
            api_id=data["api_id"],
            api_hash=data["api_hash"],
            string_session=string_session
        )
        
        result = {
            "status": "SUCCESS",
            "phone": phone,
            "telegram_id": me.id,
            "name": me.first_name or "",
            "username": me.username or "",
            "session": string_session
        }
        await client.disconnect()
        del self.pending[phone]
        return result

    async def cancel_login(self, phone):
        if phone not in self.pending:
            return
        try:
            await self.pending[phone]["client"].disconnect()
        except:
            pass
        del self.pending[phone]

    async def cleanup(self):
        phones = list(self.pending.keys())
        for phone in phones:
            try:
                await self.pending[phone]["client"].disconnect()
            except:
                pass
        self.pending.clear()

login_manager = LoginManager()
