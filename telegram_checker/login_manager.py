import asyncio
import os

from telegram_checker.backend.factory import BackendFactory, ACTIVE_ENGINE
from telegram_checker.backend.errors import (
    BackendSessionPasswordNeededError, BackendCodeExpiredError, BackendCodeInvalidError,
    BackendFloodWaitError, BackendApiIdInvalidError, BackendPhoneInvalidError, BackendError
)
from telegram_checker.session import get_session_storage

class LoginManager:
    def __init__(self):
        # الحسابات التي تنتظر إدخال الكود أو كلمة المرور
        self.pending = {}
        self.session_storage = get_session_storage()

    async def _cleanup_phone(self, phone):
        """إزالة أي جلسة سابقة لنفس الرقم"""
        if phone in self.pending:
            try:
                await self.pending[phone]["backend"].disconnect()
            except:
                pass
            del self.pending[phone]

    async def send_code(self, phone, api_id, api_hash):
        await self._cleanup_phone(phone)

        # إنشاء Backend لعملية تسجيل دخول جديدة بدون تحديد Session سابقة
        if ACTIVE_ENGINE == "tdlib":
            from telegram_checker.backend.tdlib_binding.core import TDLibClient
            client_inst = TDLibClient()
            client_inst.start()
            
            session_path = self.session_storage.get_session_path(phone)
            if not os.path.exists(session_path):
                os.makedirs(session_path, exist_ok=True)
                
            await client_inst.send({
                "@type": "setTdlibParameters",
                "use_test_dc": False,
                "database_directory": session_path,
                "use_file_database": False,
                "use_chat_info_database": False,
                "use_message_database": False,
                "api_id": int(api_id),
                "api_hash": api_hash,
                "system_language_code": "en",
                "device_model": "SM-S918B",
                "system_version": "SDK 34",
                "application_version": "10.14.5",
                "enable_storage_optimizer": True
            })
            backend = BackendFactory.create_backend(client_inst)
        else:
            from telethon import TelegramClient
            from telethon.sessions import StringSession
            client_inst = TelegramClient(
                StringSession(), 
                int(api_id), 
                api_hash,
                device_model='SM-S918B',
                system_version='SDK 34',
                app_version='10.14.5',
                lang_code='en',
                system_lang_code='en-US'
            )
            backend = BackendFactory.create_backend(client_inst)

        await backend.connect()
        try:
            result = await backend.send_code_request(phone, force_sms=True)
            
            # محاكاة إرسال رسالة SMS إذا كان الكود مرسلاً عبر التطبيق
            if result.get("type") == "SentCodeTypeApp" or result.get("type") == "App":
                await asyncio.sleep(7)
                try:
                    result = await backend.resend_code_request(phone, result.get("phone_code_hash", ""))
                except Exception as e:
                    print(f"Failed to resend code via SMS: {e}")
                    
        except BackendFloodWaitError:
            await backend.disconnect()
            raise
        except BackendApiIdInvalidError:
            await backend.disconnect()
            raise Exception("API_ID أو API_HASH غير صحيح.")
        except BackendPhoneInvalidError:
            await backend.disconnect()
            raise Exception("رقم الهاتف غير صحيح.")
        except BackendError as e:
            await backend.disconnect()
            raise Exception(str(e))
        except Exception as e:
            await backend.disconnect()
            raise e

        self.pending[phone] = {
            "backend": backend,
            "phone": phone,
            "api_id": int(api_id),
            "api_hash": api_hash,
            "phone_code_hash": result.get("phone_code_hash", "")
        }
        return True

    async def verify_code(self, phone, code):
        if phone not in self.pending:
            raise Exception("لا يوجد طلب تسجيل دخول لهذا الرقم. ابدأ العملية من جديد.")
        
        data = self.pending[phone]
        backend = data["backend"]
        try:
            await backend.sign_in_code(phone=phone, code=code, phone_code_hash=data["phone_code_hash"])
            return await self._finish_login(phone)
        except BackendSessionPasswordNeededError:
            return {"status": "PASSWORD_REQUIRED"}
        except BackendCodeExpiredError:
            try:
                result = await backend.send_code_request(phone)
                self.pending[phone]["phone_code_hash"] = result.get("phone_code_hash", "")
                return {"status": "CODE_EXPIRED", "message": "انتهت صلاحية الكود. تم إرسال كود جديد، يرجى إدخاله."}
            except Exception as e:
                await backend.disconnect()
                del self.pending[phone]
                raise Exception(f"فشل إعادة إرسال الكود: {e}")
        except BackendFloodWaitError:
            raise
        except BackendCodeInvalidError:
            raise Exception("الكود غير صحيح.")
        except BackendError as e:
            raise Exception(str(e))
        except Exception as e:
            raise Exception(str(e))

    async def verify_password(self, phone, password):
        if phone not in self.pending:
            raise Exception("لا يوجد تسجيل دخول نشط لهذا الرقم.")
        
        data = self.pending[phone]
        backend = data["backend"]
        try:
            await backend.sign_in_password(password=password)
            return await self._finish_login(phone)
        except BackendFloodWaitError:
            raise
        except BackendError as e:
            raise Exception(f"كلمة مرور التحقق بخطوتين غير صالحة. {e}")
        except Exception:
            raise Exception("كلمة مرور التحقق بخطوتين غير صالحة.")

    async def _finish_login(self, phone):
        data = self.pending[phone]
        backend = data["backend"]
        me = await backend.get_me()
        
        # استخراج بيانات الجلسة من المحرك الفعلي إن وجدت
        session_data = "tdlib_managed"
        if hasattr(backend, 'client') and hasattr(backend.client, 'session'):
            try:
                session_data = backend.client.session.save()
            except:
                pass
        
        # حفظ الجلسة باستخدام SessionStorage Abstraction Layer الموحد
        await self.session_storage.create_session(
            phone=phone,
            api_id=data["api_id"],
            api_hash=data["api_hash"],
            session_data=session_data
        )
        
        result = {
            "status": "SUCCESS",
            "phone": phone,
            "telegram_id": me.get("id"),
            "name": me.get("first_name", ""),
            "username": me.get("username", ""),
            "session": session_data
        }
        await backend.disconnect()
        del self.pending[phone]
        return result

    async def cancel_login(self, phone):
        if phone not in self.pending:
            return
        try:
            await self.pending[phone]["backend"].cancel_code(phone, self.pending[phone]["phone_code_hash"])
            await self.pending[phone]["backend"].disconnect()
        except:
            pass
        del self.pending[phone]

    async def cleanup(self):
        phones = list(self.pending.keys())
        for phone in phones:
            try:
                await self.pending[phone]["backend"].disconnect()
            except:
                pass
        self.pending.clear()

login_manager = LoginManager()
