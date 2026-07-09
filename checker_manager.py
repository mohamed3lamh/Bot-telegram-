import asyncio
import logging
import os
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    PhoneNumberBannedError,
    PhoneNumberFloodError,
    PhonePasswordProtectedError,
    PhoneMigrateError,
    FloodWaitError,
    UserDeactivatedError,
    AuthKeyUnregisteredError,
    PhoneNumberInvalidError,
    SmsCodeCreateFailedError,
    SendCodeUnavailableError,
    ForbiddenError,
    PhoneNotOccupiedError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
)
import database as db

logger = logging.getLogger(__name__)

SESSION_DIR = "checker_sessions"

# عناوين DCs الثابتة — لا تعتمد على GetConfigRequest لأنها ترجع عناوين خطأ أحياناً
DC_ADDRESSES = {
    1: ("149.154.175.10", 443),
    2: ("149.154.167.51", 443),
    3: ("149.154.175.100", 443),
    4: ("149.154.167.92", 443),
    5: ("149.154.175.50", 443),
}

class CheckerAccountClient:
    def __init__(self, db_id, api_id, api_hash, phone, is_active, is_limited):
        self.db_id = db_id
        self.api_id = api_id
        self.api_hash = api_hash
        self.phone = phone
        self.is_active = is_active
        self.is_limited = is_limited
        self.client = None
        self.connected = False
        self.total_checked = 0
        self.flood_errors = 0

class CheckerManager:
    def __init__(self):
        self.accounts: dict[int, CheckerAccountClient] = {}
        self._counter = 0
        os.makedirs(SESSION_DIR, exist_ok=True)
        try:
            self.load_from_db()
        except Exception as e:
            logger.warning(f"Failed to load checker accounts on init: {e}")

    def load_from_db(self):
        rows = db.get_all_checker_accounts()
        db_ids = {r[0] for r in rows}
        # Remove deleted accounts
        for acc_id in list(self.accounts.keys()):
            if acc_id not in db_ids:
                self.accounts.pop(acc_id)
        # Add/update existing
        for row in rows:
            acc_id, api_id, api_hash, phone, is_active, is_limited = row
            if acc_id not in self.accounts:
                self.accounts[acc_id] = CheckerAccountClient(
                    acc_id, api_id, api_hash, phone, is_active, is_limited
                )
            else:
                acc = self.accounts[acc_id]
                acc.api_id = api_id
                acc.api_hash = api_hash
                acc.phone = phone
                acc.is_active = is_active
                acc.is_limited = is_limited

    async def start_all(self):
        for acc in self.accounts.values():
            if acc.is_active and not acc.is_limited:
                await self._start_client(acc)

    async def _start_client(self, acc):
        try:
            session_path = os.path.join(SESSION_DIR, f"checker_{acc.db_id}")
            acc.client = TelegramClient(session_path, acc.api_id, acc.api_hash)
            await acc.client.connect()
            if not await acc.client.is_user_authorized():
                logger.warning(f"Checker #{acc.db_id} not authorized on current DC, scanning DCs 1-5...")
                auth_ok = False
                for dc_id in range(1, 6):
                    addr = DC_ADDRESSES.get(dc_id)
                    if not addr:
                        continue
                    ip, port = addr
                    try:
                        acc.client.session.set_dc(dc_id, ip, port)
                        await acc.client.disconnect()
                        await acc.client.connect()
                        if await acc.client.is_user_authorized():
                            auth_ok = True
                            logger.info(f"Checker #{acc.db_id} authorized on DC {dc_id}")
                            break
                    except Exception:
                        continue
                if not auth_ok:
                    logger.warning(f"Checker account #{acc.db_id} not authorized on any DC, skipping")
                    await acc.client.disconnect()
                    acc.client = None
                    return
            acc.connected = True
            logger.info(f"Checker account #{acc.db_id} ({acc.phone}) connected successfully on DC {acc.client.session._dc_id}")
        except Exception as e:
            logger.error(f"Failed to start checker account #{acc.db_id}: {e}")

    async def stop_all(self):
        for acc in self.accounts.values():
            if acc.client and acc.connected:
                try:
                    await acc.client.disconnect()
                except Exception as e:
                    logger.warning(f"Error disconnecting checker #{acc.db_id}: {e}")

    async def restart_account(self, acc_id: int):
        acc = self.accounts.get(acc_id)
        if not acc:
            return
        if acc.client and acc.connected:
            try:
                await acc.client.disconnect()
            except Exception:
                pass
        acc.connected = False
        acc.flood_errors = 0
        if acc.is_active and not acc.is_limited:
            await self._start_client(acc)

    async def check_number(self, phone_number: str,
                           durian_username: str = "",
                           durian_api_key: str = "") -> str:
        """
        يفحص الرقم ويُرجع حالته:
          - registered   : لديه حساب Telegram
          - unregistered : ليس لديه حساب
          - banned       : محظور
          - unknown      : تعذّر التحقق
        إذا تم تمرير durian_username/api_key نستخدم الـ OTP الحقيقي.
        """
        acc = self._get_next_account()
        if not acc:
            logger.warning("No active checker accounts available")
            return "unknown"

        logger.info(f"Checking {phone_number} via guest client (using credentials of checker #{acc.db_id})")
        
        guest_client = TelegramClient(StringSession(), acc.api_id, acc.api_hash)
        try:
            await asyncio.wait_for(guest_client.connect(), timeout=10.0)
            result = await self._check_with_guest(
                guest_client, phone_number, acc,
                durian_username=durian_username,
                durian_api_key=durian_api_key
            )
            acc.total_checked += 1
            return result
        except Exception as e:
            logger.warning(f"Guest check failed for {phone_number}: {e}")
            return "unknown"
        finally:
            try:
                await asyncio.wait_for(guest_client.disconnect(), timeout=3.0)
            except Exception:
                pass

    async def _read_otp_from_durian(self, username: str, api_key: str,
                                     phone_number: str, timeout_sec: int = 25) -> str:
        """
        ينتظر وصول الـ OTP عبر DurianRCS ويستخرج الكود من نص الرسالة.
        يُجرب كل 3 ثوان حتى timeout_sec ثانية.
        """
        import re
        from durian_api import DurianAPI
        deadline = asyncio.get_event_loop().time() + timeout_sec
        while asyncio.get_event_loop().time() < deadline:
            try:
                sms_result = await DurianAPI.get_sms(username, api_key, phone_number)
                if sms_result.get("status") == "success":
                    sms_text = sms_result.get("sms", "")
                    # استخراج الكود (5-6 أرقام متتالية)
                    match = re.search(r'\b(\d{5,6})\b', str(sms_text))
                    if match:
                        code = match.group(1)
                        logger.info(f"[OTP] Got real code for {phone_number}: {code}")
                        return code
            except Exception as e:
                logger.warning(f"[OTP] get_sms error for {phone_number}: {e}")
            await asyncio.sleep(3)
        logger.warning(f"[OTP] Timed out waiting for SMS for {phone_number}")
        return ""

    async def _check_with_guest(self, guest_client, phone_number, acc,
                                 durian_username: str = "",
                                 durian_api_key: str = "",
                                 retry_count: int = 0) -> str:
        if retry_count > 3:
            return "unknown"

        try:
            # الخطوة 1: إرسال طلب الكود — يكشف الحظر ويرسل OTP لـ SIM
            sent_code = await asyncio.wait_for(
                guest_client.send_code_request(phone_number),
                timeout=12.0
            )
            phone_code_hash = sent_code.phone_code_hash

            # الخطوة 2: قراءة الـ OTP الحقيقي من DurianRCS
            real_otp = ""
            if durian_username and durian_api_key:
                real_otp = await self._read_otp_from_durian(
                    durian_username, durian_api_key, phone_number
                )

            if not real_otp:
                # لم يصل الـ OTP (timeout) → نستخدم كود وهمي كملاذ أخير
                logger.warning(f"[OTP] No real OTP for {phone_number}, using fallback probe")
                real_otp = "00000"  # كود وهمي — سيُرجع خطأ لكن لن يحدد الحالة

            # الخطوة 3: استخدام الـ OTP في sign_in لتحديد الحالة
            try:
                await asyncio.wait_for(
                    guest_client.sign_in(
                        phone=phone_number,
                        code=real_otp,
                        phone_code_hash=phone_code_hash
                    ),
                    timeout=10.0
                )
                # sign_in نجح تماماً → الرقم مسجل
                logger.info(f"Result for {phone_number}: registered (sign_in succeeded with real OTP)")
                return "registered"

            except SessionPasswordNeededError:
                # سجل دخول نجح ويحتاج 2FA → مسجل
                logger.info(f"Result for {phone_number}: registered (2FA required → number exists)")
                return "registered"

            except PhoneCodeInvalidError:
                if real_otp == "00000":
                    # كود وهمي وتم رفضه → لا نستطيع تحديد الحالة
                    logger.warning(f"Result for {phone_number}: unknown (fallback OTP rejected, no real SMS received)")
                    return "unknown"
                # الكود الحقيقي خاطئ (نادر جداً) → الرقم مسجل لكن الكود فات
                logger.info(f"Result for {phone_number}: registered (real OTP expired/wrong but number exists)")
                return "registered"

            except Exception as signin_err:
                err_str = str(signin_err).upper()
                err_type = type(signin_err).__name__.upper()
                # مؤشرات الـ unregistered
                UNREGISTERED_SIGNALS = [
                    "UNOCCUPIED", "SIGN_UP", "SIGNUP",
                    "NOT_REGISTERED", "PHONE_NUMBER_UNOCCUPIED",
                    "PHONENOTOCCUPIED"
                ]
                if any(kw in err_str or kw in err_type for kw in UNREGISTERED_SIGNALS):
                    logger.info(f"Result for {phone_number}: unregistered ({type(signin_err).__name__}: {signin_err})")
                    return "unregistered"
                logger.warning(f"[SIGN_IN] {phone_number}: {type(signin_err).__name__}: {signin_err} → unknown")
                return "unknown"

        except (UserDeactivatedError, AuthKeyUnregisteredError) as deact_err:
            logger.error(f"Credentials for checker #{acc.db_id} are invalid/deactivated: {deact_err}. Disabling...")
            try:
                db.set_checker_account_active(acc.db_id, False)
            except Exception:
                pass
            acc.is_active = False
            acc.connected = False
            return "unknown"
        except PhoneNumberBannedError:
            logger.info(f"Result for {phone_number}: banned")
            return "banned"
        except PhoneNumberFloodError:
            logger.info(f"Result for {phone_number}: registered (flood on target number)")
            return "registered"
        except PhonePasswordProtectedError:
            logger.info(f"Result for {phone_number}: registered (password protected)")
            return "registered"
        except (PhoneNumberInvalidError, SmsCodeCreateFailedError, SendCodeUnavailableError) as sms_err:
            logger.info(f"Result for {phone_number}: unregistered ({sms_err.__class__.__name__}: {sms_err})")
            return "unregistered"
        except ForbiddenError as e:
            if "RECAPTCHA" in str(e) or "signup" in str(e):
                logger.info(f"Result for {phone_number}: unregistered (RECAPTCHA/signup required)")
                return "unregistered"
            logger.warning(f"ForbiddenError for {phone_number}: {e}")
            return "unknown"
        except PhoneMigrateError as e:
            logger.warning(f"PhoneMigrateError for {phone_number}, new_dc={e.new_dc}, migrating guest DC...")
            try:
                addr = DC_ADDRESSES.get(e.new_dc)
                if addr:
                    ip, port = addr
                    guest_client.session.set_dc(e.new_dc, ip, port)
                await asyncio.wait_for(guest_client.disconnect(), timeout=3.0)
                await asyncio.wait_for(guest_client.connect(), timeout=8.0)
                return await self._check_with_guest(
                    guest_client, phone_number, acc,
                    durian_username=durian_username,
                    durian_api_key=durian_api_key,
                    retry_count=retry_count + 1
                )
            except (UserDeactivatedError, AuthKeyUnregisteredError) as deact_err:
                logger.error(f"Checker account #{acc.db_id} deactivated during migration retry: {deact_err}")
                try:
                    db.set_checker_account_active(acc.db_id, False)
                except Exception:
                    pass
                acc.is_active = False
                acc.connected = False
                return "unknown"
            except PhoneNumberBannedError:
                return "banned"
            except PhoneNumberFloodError:
                return "registered"
            except PhonePasswordProtectedError:
                return "registered"
            except (PhoneNumberInvalidError, SmsCodeCreateFailedError, SendCodeUnavailableError):
                return "unregistered"
            except ForbiddenError as e2:
                if "RECAPTCHA" in str(e2) or "signup" in str(e2):
                    return "unregistered"
                return "unknown"
            except Exception as e2:
                logger.warning(f"Retry after PhoneMigrateError failed for {phone_number}: {e2}")
                return "unknown"
        except FloodWaitError as e:
            logger.warning(f"Guest client hit FloodWait: {e.seconds}s")
            acc.flood_errors += 1
            if acc.flood_errors >= 5:
                db.set_checker_account_limited(acc.db_id, True)
                acc.is_limited = True
                acc.is_active = False
                logger.warning(f"Checker account #{acc.db_id} marked as limited due to guest floods")
            return "unknown"
        except (asyncio.TimeoutError, TimeoutError) as e:
            logger.warning(f"Guest check timed out for {phone_number}")
            return "unknown"
        except Exception as e:
            logger.warning(f"Guest check got exception for {phone_number}: {type(e).__name__}: {e}")
            return "unknown"

    def _get_next_account(self):
        active = [a for a in self.accounts.values()
                  if a.is_active and not a.is_limited and a.connected]
        if not active:
            return None
        idx = self._counter % len(active)
        self._counter += 1
        return active[idx]

    async def add_account(self, api_id, api_hash, phone, session_source_path=None):
        db.add_checker_account(api_id, api_hash, phone)
        self.load_from_db()
        # العثور على الحساب المضاف حديثاً
        for acc in self.accounts.values():
            if acc.phone == phone and acc.api_id == api_id:
                if session_source_path:
                    # نسخ الجلسة المصرح بها من عملية الإعداد
                    import shutil
                    dest = os.path.join(SESSION_DIR, f"checker_{acc.db_id}.session")
                    try:
                        shutil.copy2(session_source_path, dest)
                    except Exception as e:
                        logger.warning(f"Failed to copy session for account #{acc.db_id}: {e}")
                        break
                await self._start_client(acc)
                break

    async def remove_account(self, acc_id: int):
        acc = self.accounts.get(acc_id)
        if acc and acc.client:
            try:
                await acc.client.disconnect()
            except Exception:
                pass
        db.delete_checker_account(acc_id)
        self.accounts.pop(acc_id, None)

    async def toggle_account(self, acc_id: int):
        db.toggle_checker_account(acc_id)
        self.load_from_db()
        acc = self.accounts.get(acc_id)
        if acc:
            if acc.is_active and not acc.is_limited:
                await self._start_client(acc)
            else:
                if acc.client and acc.connected:
                    try:
                        await acc.client.disconnect()
                    except Exception:
                        pass
                    acc.connected = False

    def get_status_text(self, acc_id: int) -> str:
        acc = self.accounts.get(acc_id)
        if not acc:
            return "❓ غير موجود"
        if not acc.is_active:
            return "🔴 معطل"
        if acc.is_limited:
            return "⛔ محدود"
        if not acc.connected:
            return "🟡 غير متصل"
        return "🟢 متصل"

    def get_total_checked(self, acc_id: int) -> int:
        acc = self.accounts.get(acc_id)
        return acc.total_checked if acc else 0


checker_manager = CheckerManager()
