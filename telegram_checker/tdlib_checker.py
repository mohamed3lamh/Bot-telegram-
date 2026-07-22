import asyncio
import logging
from aiotdlib import Client
from aiotdlib.api import API

logger = logging.getLogger(__name__)

class TDLibChecker:
    def __init__(self):
        self.clients = {}

    async def get_client(self, account):
        account_id = account["id"]
        if account_id in self.clients:
            return self.clients[account_id]

        client = Client(
            api_id=int(account["api_id"]),
            api_hash=account["api_hash"],
            database_encryption_key="secret",
            use_message_database=False,
            use_secret_chats=False,
            system_language_code="en",
            device_model="TDLibChecker"
        )
        
        # NOTE: aiotdlib uses phone number login differently from Telethon.
        # This will need proper session initialization.
        # We assume the session is already authenticated or needs auth.
        await client.connect()
        self.clients[account_id] = client
        return client

    async def check_phone(self, account, phone):
        try:
            client = await self.get_client(account)
            
            # Step 1: Import Contacts
            contact = API.Contact(
                phone_number=phone,
                first_name="Check",
                last_name="",
                user_id=0,
                vcard=""
            )
            
            # Try importing contact
            result = await client.api.import_contacts([contact])
            if result.user_ids and result.user_ids[0] > 0:
                logger.info(f"[TDLib] Found {phone} in contacts")
                # Remove it
                await client.api.remove_contacts(result.user_ids)
                return {"status": "HAS_SESSION", "phone": phone, "status_text": "⚠️ الرقم لديه جلسة"}
            
            # Step 2: Try to resolve it directly
            try:
                user = await client.api.search_contacts(phone, 1)
                if user.total_count > 0:
                    return {"status": "HAS_SESSION", "phone": phone, "status_text": "⚠️ الرقم لديه جلسة"}
            except Exception as e:
                pass
                
            # Step 3: Use authentication request (Honeypot/Verification)
            try:
                res = await client.api.set_authentication_phone_number(
                    phone_number=phone,
                    settings=API.PhoneNumberAuthenticationSettings(
                        allow_flash_call=False,
                        is_current_phone_number=False,
                        allow_sms_retriever_api=False
                    )
                )
                
                # If we get here, the code was sent, meaning the number exists
                # We need to cancel the code to avoid annoying the user
                try:
                    await client.api.resend_authentication_code() # This is a placeholder, TDLib cancellation varies
                except:
                    pass
                    
                return {"status": "HAS_SESSION", "phone": phone, "status_text": "⚠️ الرقم لديه جلسة"}
                
            except Exception as auth_err:
                err_msg = str(auth_err).upper()
                if "PHONE_NUMBER_UNOCCUPIED" in err_msg or "PHONE_NUMBER_INVALID" in err_msg:
                    return {"status": "NO_SESSION", "phone": phone, "status_text": "🆕 غير مسجل"}
                if "PHONE_NUMBER_BANNED" in err_msg:
                    return {"status": "BANNED", "phone": phone, "status_text": "📵 مـحـظـور"}
                if "FLOOD_WAIT" in err_msg:
                    return {"status": "FLOOD_WAIT", "seconds": 60, "phone": phone, "status_text": f"🚫 حظر مؤقت"}
                
                return {"status": "ERROR", "phone": phone, "status_text": f"⚙️ خطأ: {auth_err}"}

        except Exception as e:
            logger.error(f"[TDLib] Exception in check_phone: {type(e).__name__} - {e}", exc_info=True)
            return {"status": "ERROR", "phone": phone, "status_text": f"❌ فشل فحص TDLib: {e}"}

tdlib_checker = TDLibChecker()
