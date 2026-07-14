import asyncio
import os
import logging
import time
import traceback
import datetime
from telethon import functions, types
from telethon.errors import (
    FloodWaitError, UserPrivacyRestrictedError, PhoneNumberBannedError,
    SessionPasswordNeededError, PhoneNumberInvalidError,
    PhoneNumberUnoccupiedError, PhoneMigrateError, PhoneCodeInvalidError
)
from telethon.tl.types.auth import (
    SentCodeTypeApp, SentCodeTypeSms, SentCodeTypeFlashCall, SentCodeTypeMissedCall, SentCodeTypeEmailCode
)
from .telegram_client import telegram_client_manager, SessionUnauthorizedError
from .account_manager import account_manager
from .flood_manager import flood_manager
from proxy_infrastructure import proxy_manager

logger = logging.getLogger(__name__)

# =====================================================================
# Strategy Pattern: base abstract class and individual strategies
# =====================================================================

class SmartCheckStrategy:
    """
    نظام الفحص الرباعي الهجين الفائق الدقة (Smart Quad-Layer Checker):
    1. الطبقة الأولى: الاستيراد الصامت (ImportContactsRequest) - فحص سريع وصامت.
    2. الطبقة الثانية: فحص الخادم المباشر وتحديد الخصوصية (ResolvePhoneRequest) - لمعالجة قيود الخصوصية والتفريق الدقيق.
    3. الطبقة الثالثة: فحص التدفق بالكود التجريبي (send_code_request) - الملاذ الأخير الحاسم لتحديد وجود التطبيق (App vs SMS) مع إلغاء الكود فوراً وبشكل حاسم لتجنب إرسال أي رسالة للمستهدف.
    """
    async def check(self, client, phone, account):
        # تجميع نتائج الطبقات لضمان عدم حدوث تضارب في النتيجة النهائية
        layer_results = {}

        # --- الطبقة الأولى: الاستيراد الصامت (Silent Import) ---
        logger.info(f"[Layer 1: Import] Silent Contact Import check for {phone}")
        try:
            contact = types.InputPhoneContact(client_id=0, phone=phone, first_name="TempCheck", last_name="")
            import_res = await asyncio.wait_for(
                client(functions.contacts.ImportContactsRequest(contacts=[contact])),
                timeout=8.0
            )
            
            # تنظيف قائمة جهات الاتصال فوراً
            if import_res.users:
                user_id = import_res.users[0].id
                await client(functions.contacts.DeleteContactsRequest(id=[user_id]))
                logger.info(f"[Layer 1] User found directly! Registered. (Phone: {phone})")
                layer_results["layer1"] = "HAS_SESSION"
                return {"status": "HAS_SESSION", "phone": phone, "status_text": "⚠️ مسجل"}
            
            elif import_res.imported:
                imported_user_id = import_res.imported[0].user_id
                await client(functions.contacts.DeleteContactsRequest(id=[imported_user_id]))
                logger.info(f"[Layer 1] Contact imported! Registered. (Phone: {phone})")
                layer_results["layer1"] = "HAS_SESSION"
                return {"status": "HAS_SESSION", "phone": phone, "status_text": "⚠️ مسجل"}

        except PhoneMigrateError as e:
            # معالجة فورية لانتقال مركز البيانات
            logger.info(f"[Layer 1] Phone migrate detected to DC {e.new_dc}. Re-routing...")
            try:
                await telegram_client_manager.disconnect_client(account["id"])
                client2 = await telegram_client_manager.get_client(account)
                await client2._switch_dc(e.new_dc)
                await asyncio.sleep(0.5)
                return await self.check(client2, phone, account)
            except Exception as migrate_error:
                logger.error(f"Migration error: {migrate_error}")
                return {"status": "ERROR", "phone": phone, "status_text": f"❌ فشل الانتقال لـ DC {e.new_dc}"}

        except FloodWaitError as e:
            await flood_manager.set_flood(account["id"], e.seconds)
            return {
                "status": "FLOOD_WAIT",
                "seconds": e.seconds,
                "phone": phone,
                "status_text": f"🚫 حظر مؤقت {e.seconds} ثانية"
            }
        except Exception as e:
            error_message = str(e).upper()
            logger.warning(f"[Layer 1] Silent Phase error: {e}")
            if "BANNED" in error_message or "AUTH_KEY_UNREGISTERED" in error_message:
                await account_manager.disable_account(account["id"])
                return {"status": "ACCOUNT_DISABLED", "phone": phone, "status_text": "❌ حساب الفاحص تالف وتم تعطيله"}

        # --- الطبقة الثانية: فحص الخصوصية والتحقق المباشر (ResolvePhone) ---
        logger.info(f"[Layer 2: ResolvePhone] Running ResolvePhoneRequest for {phone}")
        try:
            resolved = await client(functions.contacts.ResolvePhoneRequest(phone=phone))
            if resolved.users:
                logger.info(f"[Layer 2] User resolved successfully! Registered. (Phone: {phone})")
                layer_results["layer2"] = "HAS_SESSION"
                return {"status": "HAS_SESSION", "phone": phone, "status_text": "⚠️ مسجل"}
            else:
                logger.info(f"[Layer 2] ResolvePhone returned empty user. Moving to Layer 3...")

        except UserPrivacyRestrictedError:
            # مستخدم مسجل ولكن قام بتشديد إعدادات الخصوصية (دليل قاطع على وجود الحساب!)
            logger.info(f"[Layer 2] Privacy Restricted! Phone is Registered but hidden. (Phone: {phone})")
            layer_results["layer2"] = "HAS_SESSION"
            return {"status": "HAS_SESSION", "phone": phone, "status_text": "⚠️ مسجل"}

        except PhoneNumberUnoccupiedError:
            logger.info(f"[Layer 2] Phone unoccupied. Not registered. (Phone: {phone})")
            layer_results["layer2"] = "NO_SESSION"
            # السماح بالمرور للطبقة الثالثة كخطوة تأكيد

        except PhoneNumberBannedError:
            logger.info(f"[Layer 2] Phone is banned. (Phone: {phone})")
            return {
                "status": "BANNED",
                "phone": phone,
                "status_text": "📵 محظور"
            }

        except PhoneNumberInvalidError:
            logger.info(f"[Layer 2] Phone invalid. (Phone: {phone})")
            layer_results["layer2"] = "NO_SESSION"
            # السماح بالمرور للطبقة الثالثة كخطوة تأكيد
            # تم إزالة الإرجاع المبكر

        except FloodWaitError as e:
            await flood_manager.set_flood(account["id"], e.seconds)
            return {
                "status": "FLOOD_WAIT",
                "seconds": e.seconds,
                "phone": phone,
                "status_text": f"🚫 حظر مؤقت {e.seconds} ثانية"
            }

        except Exception as e:
            error_str = str(e).upper()
            error_type = type(e).__name__.upper()
            logger.warning(f"[Layer 2] ResolvePhone error: {e}")

            # فحص الكلمات المفتاحية للخطأ للتعامل الدقيق
            NO_SESSION_KEYWORDS = ["UNOCCUPIED", "NO USER", "NOT FOUND", "NOT_FOUND", "NO_PHONE_ASSOCIATED"]
            BANNED_KEYWORDS = ["BANNED", "PHONE_NUMBER_BANNED"]
            PRIVACY_KEYWORDS = ["PRIVACY", "PRIVACY_RESTRICTED", "USERPRIVACYRESTRICTED"]

            if any(kw in error_str or kw in error_type for kw in PRIVACY_KEYWORDS):
                logger.info(f"[Layer 2] Privacy error detected. Phone is Registered. (Phone: {phone})")
                layer_results["layer2"] = "HAS_SESSION"
                return {"status": "HAS_SESSION", "phone": phone, "status_text": "⚠️ مسجل"}

            elif any(kw in error_str or kw in error_type for kw in NO_SESSION_KEYWORDS):
                logger.info(f"[Layer 2] Keyword 'NOT_FOUND' detected. (Phone: {phone})")
                layer_results["layer2"] = "NO_SESSION"

            elif any(kw in error_str or kw in error_type for kw in BANNED_KEYWORDS):
                logger.info(f"[Layer 2] Keyword match: Banned. (Phone: {phone})")
                return {
                    "status": "BANNED",
                    "phone": phone,
                    "status_text": "📵 محظور"
                }

            elif "AUTH_KEY" in error_str:
                await account_manager.disable_account(account["id"])
                return {"status": "ACCOUNT_DISABLED", "phone": phone, "status_text": "❌ حساب الفاحص تالف وتم تعطيله"}

        # --- اختبار الفخ (Honeypot Test) لتأكيد حظر الظل ---
        import database as db
        honeypot_number = await asyncio.to_thread(db.get_setting, "honeypot_number")
        if honeypot_number and layer_results.get("layer1") == "NO_SESSION" and layer_results.get("layer2") == "NO_SESSION":
            if phone != honeypot_number:
                if not hasattr(self, "_honeypot_cache"):
                    self._honeypot_cache = {}
                
                last_verified = self._honeypot_cache.get(account["id"], 0)
                if time.monotonic() - last_verified > 300: # 5 minutes
                    logger.info(f"[Honeypot] Testing account {account['id']} with honeypot {honeypot_number}...")
                    found_hp = False
                    try:
                        hp_contact = types.InputPhoneContact(client_id=0, phone=honeypot_number, first_name="TempHP", last_name="")
                        hp_res = await asyncio.wait_for(
                            client(functions.contacts.ImportContactsRequest(contacts=[hp_contact])),
                            timeout=6.0
                        )
                        if getattr(hp_res, 'users', None):
                            found_hp = True
                            await client(functions.contacts.DeleteContactsRequest(id=[hp_res.users[0].id]))
                        elif getattr(hp_res, 'imported', None):
                            found_hp = True
                            await client(functions.contacts.DeleteContactsRequest(id=[hp_res.imported[0].user_id]))
                        
                        if not found_hp:
                            hp_res2 = await client(functions.contacts.ResolvePhoneRequest(phone=honeypot_number))
                            if getattr(hp_res2, 'users', None):
                                found_hp = True
                    except Exception as hp_err:
                        logger.warning(f"[Honeypot] Check error: {hp_err}")
                        
                    if not found_hp:
                        if not hasattr(self, "_shadowban_strikes"):
                            self._shadowban_strikes = {}
                        
                        strikes = self._shadowban_strikes.get(account["id"], 0) + 1
                        self._shadowban_strikes[account["id"]] = strikes
                        
                        if strikes >= 2:
                            logger.error(f"[Honeypot] 🚨 Account {account['id']} FAILED honeypot for the SECOND time! Deleting it completely!")
                            await asyncio.to_thread(db.delete_telegram_account, account["id"])
                            account_manager.invalidate_accounts_cache()
                            return {"status": "ERROR", "phone": phone, "status_text": "❌ الحساب تالف وتم حذفه نهائياً!"}
                        else:
                            logger.error(f"[Honeypot] 🚨 Account {account['id']} FAILED the honeypot test! Setting 24h rest period.")
                            await flood_manager.set_flood(account["id"], 24 * 3600)
                            return {"status": "ERROR", "phone": phone, "status_text": "❌ الحساب في فترة استشفاء (24 ساعة)"}
                    else:
                        logger.info(f"[Honeypot] ✅ Account {account['id']} passed the honeypot test.")
                        self._honeypot_cache[account["id"]] = time.monotonic()
                        # تصفير المخالفات إذا تعافى الحساب
                        if hasattr(self, "_shadowban_strikes") and account["id"] in self._shadowban_strikes:
                            self._shadowban_strikes[account["id"]] = 0

        # --- الطبقة الثالثة: فحص التدفق بالكود التجريبي (send_code_request) ---
        # يستخدم بروكسي من دولة الرقم لضمان الدقة وتجنب حماية تيليجرام المضادة للبوتات
        logger.info(f"[Layer 3: SendCode] Running send_code_request for {phone}")

        session_id = f"check_{phone}_{int(time.time())}"
        proxy_tuple = await proxy_manager.get_proxy_for_telegram(phone, session_id)
        proxy_client = None
        active_client = client  # الافتراضي: العميل الحالي بدون بروكسي
        used_proxy = False

        if proxy_tuple is not None:
            logger.info(f"[Layer 3] Proxy found. Connecting via proxy...")
            try:
                proxy_client = await telegram_client_manager.get_client(account, proxy=proxy_tuple)
                active_client = proxy_client
                used_proxy = True
                logger.info(f"[Layer 3] Proxy client connected successfully for {phone}.")
            except Exception as proxy_err:
                logger.warning(f"[Layer 3] Proxy connection failed ({proxy_err}). Falling back to direct connection.")
                proxy_client = None
                active_client = client
                used_proxy = False
        else:
            logger.info(f"[Layer 3] No proxy found or available. Using direct connection.")

        is_success = False
        is_flood = False
        try:
            # التأكد من أن العميل متصل بنجاح قبل إرسال الطلب
            if not active_client.is_connected():
                logger.info(f"[Layer 3] Client disconnected for {phone}. Reconnecting...")
                await active_client.connect()

            try:
                # نرسل طلب توليد كود. تيليجرام سيفحص أولاً إذا كان الرقم له حساب نشط
                result = await active_client(functions.auth.SendCodeRequest(
                    phone_number=phone,
                    api_id=int(account["api_id"]),
                    api_hash=account["api_hash"],
                    settings=types.CodeSettings(allow_flashcall=False, current_number=True, allow_app_hash=True)
                ))
            except PhoneMigrateError:
                raise
            except Exception as send_err:
                # إذا فشل إرسال الطلب عبر البروكسي (بسبب انقطاع اتصاله أو تعطل البروكسي)
                # نحاول عمل Rotation للبروكسي إذا كان مدعوماً
                if used_proxy:
                    logger.warning(f"[Layer 3] Request via proxy failed for {phone} ({send_err}). Trying IP rotation...")
                    rotated = await proxy_manager.trigger_rotation(session_id)
                    if rotated:
                        # إعادة إنشاء العميل بالبروكسي الجديد ومحاولة الاتصال مرة أخرى
                        try:
                            if proxy_client is not None:
                                await proxy_client.disconnect()
                            proxy_client = await telegram_client_manager.get_client(account, proxy=proxy_tuple)
                            active_client = proxy_client
                            if not active_client.is_connected():
                                await active_client.connect()
                            result = await active_client(functions.auth.SendCodeRequest(
                                phone_number=phone,
                                api_id=int(account["api_id"]),
                                api_hash=account["api_hash"],
                                settings=types.CodeSettings(allow_flashcall=False, current_number=True, allow_app_hash=True)
                            ))
                            logger.info(f"[Layer 3] Request succeeded after proxy IP rotation.")
                        except Exception as retry_err:
                            logger.warning(f"[Layer 3] Retry after proxy rotation failed: {retry_err}. Falling back to direct.")
                            used_proxy = False
                            active_client = client
                            if not active_client.is_connected():
                                await active_client.connect()
                            result = await active_client(functions.auth.SendCodeRequest(
                                phone_number=phone,
                                api_id=int(account["api_id"]),
                                api_hash=account["api_hash"],
                                settings=types.CodeSettings(allow_flashcall=False, current_number=True, allow_app_hash=True)
                            ))
                    else:
                        logger.warning(f"[Layer 3] Rotation not supported or failed. Falling back to direct connection...")
                        if proxy_client is not None:
                            try:
                                await proxy_client.disconnect()
                            except Exception:
                                pass
                            proxy_client = None
                        active_client = client
                        used_proxy = False
                        if not active_client.is_connected():
                            await active_client.connect()
                        result = await active_client(functions.auth.SendCodeRequest(
                            phone_number=phone,
                            api_id=int(account["api_id"]),
                            api_hash=account["api_hash"],
                            settings=types.CodeSettings(allow_flashcall=False, current_number=True, allow_app_hash=True)
                        ))
                else:
                    raise send_err

            code_type = type(result.type)
            logger.info(f"[Layer 3] Response code type for {phone}: {code_type.__name__} (via {'proxy' if used_proxy else 'direct'})")



            # إلغاء الكود فوراً لمنع وصوله للمستهدف
            try:
                await active_client(functions.auth.CancelCodeRequest(
                    phone_number=phone,
                    phone_code_hash=result.phone_code_hash
                ))
                logger.info(f"[Layer 3] Cancelled verification code for {phone} successfully.")
            except Exception as cancel_err:
                logger.warning(f"[Layer 3] CancelCodeRequest failed (safe to ignore): {cancel_err}")

            is_success = True

            # --- تحليل النتيجة بدقة ---
            if used_proxy:
                # عند الاتصال عبر proxy مطابق لدولة الرقم:
                # SentCodeTypeApp → مسجل بجلسة نشطة
                # SentCodeTypeEmailCode → مسجل بدون جلسة (يُرسل للبريد)
                # SentCodeTypeSms/Flash/MissedCall → مسجل بدون جلسة نشطة (يُرسل SMS)
                # نتائج دقيقة 100% لأن الـ proxy يمنع حماية تيليجرام المضادة
                if code_type == SentCodeTypeApp:
                    logger.info(f"[Layer 3+Proxy] App code → Registered with active session. (Phone: {phone})")
                    return {"status": "HAS_SESSION", "phone": phone, "status_text": "⚠️ مسجل"}
                elif code_type == SentCodeTypeEmailCode:
                    logger.info(f"[Layer 3+Proxy] Email code → Registered (no active session). (Phone: {phone})")
                    return {"status": "HAS_SESSION", "phone": phone, "status_text": "⚠️ مسجل"}
                else:
                    # SMS / FlashCall / MissedCall عبر proxy = مسجل بدون جلسة تطبيق
                    logger.info(f"[Layer 3+Proxy] SMS/Flash code → Registered (no app session). (Phone: {phone})")
                    return {"status": "HAS_SESSION", "phone": phone, "status_text": "⚠️ مسجل"}
            else:
                # بدون proxy: بما أن خوادم تيليجرام تخدع الفاحص وتعيد SentCodeTypeApp دائماً،
                # سنقوم بمراجعة نتائج الطبقة الأولى والثانية بدلاً من الافتراض الأعمى أنه غير مسجل.
                if layer_results.get("layer1") == "HAS_SESSION" or layer_results.get("layer2") == "HAS_SESSION":
                    logger.info(f"[Layer 3] Direct connection returned code, but Layer 1/2 confirmed it's registered. Returning HAS_SESSION. (Phone: {phone})")
                    return {"status": "HAS_SESSION", "phone": phone, "status_text": "⚠️ مسجل"}
                else:
                    logger.info(f"[Layer 3] Direct connection returned code, and previous layers didn't find it. Cannot determine accuracy. (Phone: {phone})")
                    return {"status": "INACCURATE", "phone": phone, "status_text": "⚠️ فحص ليس دقيق"}

        except PhoneNumberUnoccupiedError:
            logger.info(f"[Layer 3] Unoccupied error. Phone is Not Registered. (Phone: {phone})")
            is_success = True
            return {
                "status": "NO_SESSION",
                "phone": phone,
                "status_text": "🆕 غير مسجل"
            }

        except PhoneNumberBannedError:
            logger.info(f"[Layer 3] Banned error. Phone is Banned. (Phone: {phone})")
            is_success = True
            return {
                "status": "BANNED",
                "phone": phone,
                "status_text": "📵 محظور"
            }

        except PhoneNumberInvalidError:
            logger.info(f"[Layer 3] Invalid phone. Phone is Not Registered. (Phone: {phone})")
            is_success = True
            return {
                "status": "NO_SESSION",
                "phone": phone,
                "status_text": "🆕 غير مسجل"
            }

        except FloodWaitError as e:
            await flood_manager.set_flood(account["id"], e.seconds)
            logger.warning(f"[Layer 3] FloodWait: {e.seconds} seconds on checker.")
            is_flood = True
            return {
                "status": "FLOOD_WAIT",
                "seconds": e.seconds,
                "phone": phone,
                "status_text": f"🚫 حظر مؤقت {e.seconds} ثانية"
            }

        except SessionPasswordNeededError:
            # إذا طلب الباسورد، فهذا يعني أن الحساب موجود ومحمي بالتحقق بخطوتين -> مسجل قطعا
            logger.info(f"[Layer 3] Session password needed! Phone is Registered. (Phone: {phone})")
            is_success = True
            return {
                "status": "HAS_SESSION",
                "phone": phone,
                "status_text": "⚠️ مسجل"
            }

        except PhoneMigrateError as e:
            # الرقم مسجل في DC مختلف عن DC الفاحص الحالي → ننتقل للـ DC الصحيح ونُعيد المحاولة
            logger.info(f"[Layer 3] PhoneMigrateError to DC {e.new_dc}. Re-routing and retrying send_code... (Phone: {phone})")
            try:
                await active_client._switch_dc(e.new_dc)
                await asyncio.sleep(0.5)

                result2 = await active_client(functions.auth.SendCodeRequest(
                    phone_number=phone,
                    api_id=int(account["api_id"]),
                    api_hash=account["api_hash"],
                    settings=types.CodeSettings(allow_flashcall=False, current_number=True, allow_app_hash=True)
                ))
                code_type2 = type(result2.type)
                logger.info(f"[Layer 3] After DC migration: code type = {code_type2.__name__} (Phone: {phone})")

                # إلغاء الكود فوراً بعد الانتقال أيضاً
                try:
                    await active_client(functions.auth.CancelCodeRequest(
                        phone_number=phone,
                        phone_code_hash=result2.phone_code_hash
                    ))
                    logger.info(f"[Layer 3] Cancelled code after DC migration for {phone}.")
                except Exception as cancel_err:
                    logger.warning(f"[Layer 3] CancelCodeRequest after migration failed (safe): {cancel_err}")

                is_success = True
                
                # أي نوع كود (App / Email / SMS / Flash) يُثبت أن الرقم مسجل
                if used_proxy:
                    if code_type2 in (SentCodeTypeApp, SentCodeTypeEmailCode):
                        logger.info(f"[Layer 3+Proxy] After DC migration: App/Email → Registered. (Phone: {phone})")
                    else:
                        logger.info(f"[Layer 3+Proxy] After DC migration: SMS/Flash → Registered (no active app session). (Phone: {phone})")
                    return {
                        "status": "HAS_SESSION",
                        "phone": phone,
                        "status_text": "⚠️ مسجل"
                    }
                else:
                    if layer_results.get("layer1") == "HAS_SESSION" or layer_results.get("layer2") == "HAS_SESSION":
                        logger.info(f"[Layer 3] After DC migration (Direct connection) returned code, but Layer 1/2 confirmed it's registered. Returning HAS_SESSION. (Phone: {phone})")
                        return {"status": "HAS_SESSION", "phone": phone, "status_text": "⚠️ مسجل"}
                    else:
                        logger.info(f"[Layer 3] After DC migration (Direct connection) returned code, and previous layers didn't find it. Cannot determine accuracy. (Phone: {phone})")
                        return {"status": "INACCURATE", "phone": phone, "status_text": "⚠️ فحص ليس دقيق"}

            except PhoneNumberBannedError:
                logger.info(f"[Layer 3] After DC migration: Phone is Banned. (Phone: {phone})")
                is_success = True
                return {"status": "BANNED", "phone": phone, "status_text": "📵 محظور"}

            except PhoneNumberUnoccupiedError:
                logger.info(f"[Layer 3] After DC migration: Phone is Not Registered. (Phone: {phone})")
                is_success = True
                return {"status": "NO_SESSION", "phone": phone, "status_text": "🆕 غير مسجل"}

            except FloodWaitError as fe:
                await flood_manager.set_flood(account["id"], fe.seconds)
                logger.warning(f"[Layer 3] FloodWait after DC migration: {fe.seconds}s. (Phone: {phone})")
                is_flood = True
                return {"status": "FLOOD_WAIT", "seconds": fe.seconds, "phone": phone, "status_text": f"🚫 حظر مؤقت {fe.seconds} ثانية"}

            except Exception as migrate_err:
                logger.error(f"[Layer 3] DC migration failed for {phone}: {migrate_err}")
                return {"status": "ERROR", "phone": phone, "status_text": f"❌ فشل الانتقال لـ DC {e.new_dc}"}

        except Exception as e:
            error_str = str(e).upper()
            logger.error(f"[Layer 3] Unexpected exception for {phone}: {e}")
            
            if any(kw in error_str for kw in ["UNOCCUPIED", "NO USER", "NOT FOUND", "NOT_FOUND"]):
                is_success = True
                return {"status": "NO_SESSION", "phone": phone, "status_text": "🆕 غير مسجل"}
            if "BANNED" in error_str:
                is_success = True
                return {"status": "BANNED", "phone": phone, "status_text": "📵 محظور"}
            if "AUTH_KEY" in error_str:
                await account_manager.disable_account(account["id"])
                return {"status": "ACCOUNT_DISABLED", "phone": phone, "status_text": "❌ حساب الفاحص تالف وتم تعطيله"}

            # كخيار أمان أخير لمنع تصنيف الأخطاء العشوائية كغير مسجل
            return {
                "status": "ERROR",
                "phone": phone,
                "status_text": f"⚙️ خطأ نظام: {e}"
            }

        finally:
            # تحرير البروكسي وإرسال إحصائيات الجودة
            await proxy_manager.release_proxy(session_id, is_success, is_flood)

            # إغلاق عميل البروكسي المؤقت بعد انتهاء الفحص لتجنب تسرب الاتصالات
            if proxy_client is not None:
                try:
                    await proxy_client.disconnect()
                except Exception:
                    pass



# =====================================================================
# Main Engine: TelegramCheckEngine
# =====================================================================

class TelegramCheckEngine:
    def __init__(self):
        self.strategy = SmartCheckStrategy()

    async def check_phone(self, account, phone):
        t_start = time.perf_counter()
        logger.info(f"Starting check for {phone} using checker {account.get('id')}")

        try:
            client = await telegram_client_manager.get_client(account)
            logger.info("Connected Successfully")
        except SessionUnauthorizedError:
            await account_manager.disable_account(account["id"])
            t_end = time.perf_counter()
            return {
                "status": "ACCOUNT_DISABLED",
                "phone": phone,
                "status_text": "❌ حساب الفاحص تالف وتم تعطيله"
            }
        except Exception as e:
            logger.error(f"Connection Failed: {e}")
            t_end = time.perf_counter()
            return {
                "status": "ERROR",
                "phone": phone,
                "status_text": f"❌ فشل الاتصال بالفاحص: {e}"
            }

        res = await self.strategy.check(client, phone, account)
        if res and res.get("status") not in ["FLOOD_WAIT", "ACCOUNT_DISABLED", "ERROR"]:
            try:
                await flood_manager.account_used(account["id"])
            except Exception as ue:
                logger.error(f"Failed to increment checks count for account {account.get('id')}: {ue}")
        t_end = time.perf_counter()
        logger.info(f"End check for {phone}. Execution time: {t_end - t_start:.4f}s")
        return res


# =====================================================================
# Legacy Adapter for Backward Compatibility
# =====================================================================

class TelegramChecker:
    def __init__(self):
        self.engine = TelegramCheckEngine()

    async def _auto_recovery_loop(self):
        """
        حلقة خلفية تعمل كل 5 دقائق لمحاولة استعادة الحسابات المعطلة
        وإعادتها للخدمة تلقائياً في حال عادت للعمل.
        """
        await asyncio.sleep(30)  # الانتظار قليلاً عند بدء التشغيل لعدم التضارب
        while True:
            try:
                disabled_accounts = await account_manager.get_all_disabled_accounts()
                if disabled_accounts:
                    logger.info(f"[Auto-Recovery] Found {len(disabled_accounts)} disabled accounts. Testing recovery...")
                    for acc in disabled_accounts:
                        try:
                            # محاولة تهيئة الاتصال بدون إثارة أخطاء
                            client = await telegram_client_manager.get_client(acc)
                            if await client.is_user_authorized():
                                logger.info(f"[Auto-Recovery] Account {acc['phone']} is authorized! Recovering...")
                                await account_manager.enable_account(acc["id"])
                        except SessionUnauthorizedError:
                            pass
                        except Exception as e:
                            logger.warning(f"[Auto-Recovery] Failed recovery check for {acc['phone']}: {e}")
            except Exception as e:
                logger.error(f"[Auto-Recovery] Error in recovery loop: {e}")
            
            await asyncio.sleep(300) # فحص كل 5 دقائق

    async def get_available_account(self):
        if not hasattr(self, "_recovery_task_started"):
            self._recovery_task_started = True
            asyncio.create_task(self._auto_recovery_loop())
        return await account_manager.get_available_account()

    async def wait_for_account(self):
        """
        Smart Wait: If all checkers are in FloodWait, sleeps precisely 
        until the earliest flooded account becomes free.
        """
        while True:
            account = await self.get_available_account()
            if account:
                return account
            
            sleep_time = await account_manager.get_seconds_until_next_available()
            logger.warning(f"[Checker] All accounts in FloodWait or disabled. Sleeping smartly for {sleep_time:.2f}s...")
            await asyncio.sleep(sleep_time)

    async def check_phone(self, account, phone):
        if not hasattr(self, "_recovery_task_started"):
            self._recovery_task_started = True
            asyncio.create_task(self._auto_recovery_loop())
        return await self.engine.check_phone(account, phone)

    async def check_numbers(self, phones, callback=None):
        results = []
        for phone in phones:
            account = await self.wait_for_account()
            result = await self.check_phone(account, phone)
            
            # Retry if the current checker account hits Flood or gets deactivated during the request
            if result["status"] in ["FLOOD_WAIT", "ACCOUNT_DISABLED"]:
                account_retry = await self.wait_for_account()
                result = await self.check_phone(account_retry, phone)
                
            results.append(result)
            if callback:
                await callback(result)
        return results


class BatchChecker:
    def __init__(self, checker):
        self.checker = checker

    async def worker(self, account, queue, callback=None, active_workers=None):
        try:
            while True:
                phone = await queue.get()

                if phone is None:
                    queue.task_done()
                    break

                result = await self.checker.check_phone(account, phone)

                if result["status"] in ["FLOOD_WAIT", "FLOOD"]:
                    seconds = result.get("seconds", 60)
                    await flood_manager.set_flood(account["id"], seconds)
                    queue.put_nowait(phone)
                    queue.task_done()
                    break

                elif result["status"] in ["ACCOUNT_DISABLED", "CHECKER_BANNED"]:
                    queue.put_nowait(phone)
                    queue.task_done()
                    break

                if callback:
                    await callback(result)

                queue.task_done()

        finally:
            if active_workers is not None:
                async with active_workers["lock"]:
                    active_workers["count"] -= 1
                    is_last = active_workers["count"] <= 0

                if is_last:
                    drained = 0
                    while not queue.empty():
                        try:
                            queue.get_nowait()
                            queue.task_done()
                            drained += 1
                        except asyncio.QueueEmpty:
                            break

                    if drained:
                        logger.warning(
                            f"BatchChecker: تم تفريغ {drained} رقم من الطابور بدون فحص."
                        )

    async def run(self, phones, callback=None):
        queue = asyncio.Queue()

        for phone in phones:
            await queue.put(phone)

        accounts = await account_manager.get_all_accounts()

        workers = []
        active_workers = {"count": 0, "lock": asyncio.Lock()}

        for account in accounts:
            if await flood_manager.is_flooded(account["id"]):
                continue

            active_workers["count"] += 1

            task = asyncio.create_task(
                self.worker(account, queue, callback, active_workers)
            )
            workers.append(task)

        if not workers:
            logger.warning("BatchChecker: لا توجد حسابات فاحصة متاحة.")
            return False

        await queue.join()

        for _ in workers:
            await queue.put(None)

        await asyncio.gather(*workers, return_exceptions=True)

        return True


telegram_checker = TelegramChecker()
batch_checker = BatchChecker(telegram_checker)
