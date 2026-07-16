import re

with open('telegram_checker/checker.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Replace check_phone with cache logic
check_phone_old = """    async def check_phone(self, account, phone):
        t_start = time.perf_counter()
        logger.info(f"Starting check for {phone} using checker {account.get('id')}")"""

check_phone_new = """    async def check_phone(self, account, phone):
        import database as db
        cached = await asyncio.to_thread(db.get_cached_number, phone)
        if cached:
            logger.info(f"Returning cached result for {phone}: {cached['status']}")
            return cached

        t_start = time.perf_counter()
        logger.info(f"Starting check for {phone} using checker {account.get('id')}")"""

content = content.replace(check_phone_old, check_phone_new)

# 2. Add cache saving logic
save_cache_old = """        res = await self.strategy.check(client, phone, account)
        if res and res.get("status") not in ["FLOOD_WAIT", "ACCOUNT_DISABLED", "ERROR"]:
            try:
                await flood_manager.account_used(account["id"])"""

save_cache_new = """        res = await self.strategy.check(client, phone, account)
        
        if res and res.get("status") in ["HAS_SESSION", "NO_SESSION", "BANNED"]:
            await asyncio.to_thread(db.save_cached_number, res["phone"], res["status"], res["status_text"])
            
        if res and res.get("status") not in ["FLOOD_WAIT", "ACCOUNT_DISABLED", "ERROR"]:
            try:
                await flood_manager.account_used(account["id"])"""

content = content.replace(save_cache_old, save_cache_new)

# 3. Replace Layer 3 and 4 logic in SmartCheckStrategy.check
# We will match from "# --- الطبقة الثالثة (البديلة): بوت فحص خارجي ---" 
# to the end of "finally:" block.
pattern = r"# --- الطبقة الثالثة \(البديلة\): بوت فحص خارجي ---.*?finally:\n.*?pass\n"

replacement = """# --- الطبقة الثالثة: فحص التدفق بالكود التجريبي (send_code_request) مجاني ومباشر ---
        logger.info(f"[Layer 3: SendCode] Running direct send_code_request for {phone}")
        is_success = False
        is_flood = False
        try:
            if not client.is_connected():
                await client.connect()

            result = await client(functions.auth.SendCodeRequest(
                phone_number=phone,
                api_id=int(account["api_id"]),
                api_hash=account["api_hash"],
                settings=types.CodeSettings(allow_flashcall=False, current_number=True, allow_app_hash=True)
            ))
            
            # إلغاء الكود فوراً
            try:
                await client(functions.auth.CancelCodeRequest(
                    phone_number=phone,
                    phone_code_hash=result.phone_code_hash
                ))
            except Exception:
                pass
            
            logger.info(f"[Layer 3] Direct connection returned code. Deferring to Layer 4 (External Bot).")
            
            # --- الطبقة الرابعة: بوت فحص خارجي ---
            checker_bot = await asyncio.to_thread(db.get_setting, "checker_bot_username")
            if checker_bot:
                logger.info(f"[Layer 4: ExternalBot] Checking {phone} via @{checker_bot}...")
                ext_result = await self._check_via_external_bot(client, phone, checker_bot)
                if ext_result is not None:
                    is_success = True
                    return ext_result
                logger.warning(f"[Layer 4: ExternalBot] Failed to get clear response.")
                return {"status": "INACCURATE", "phone": phone, "status_text": "⚠️ فحص ليس دقيق (فشل البوت الخارجي)"}
            else:
                logger.warning(f"[Layer 4: ExternalBot] Not configured. Cannot determine accuracy.")
                return {"status": "INACCURATE", "phone": phone, "status_text": "⚠️ فحص ليس دقيق (يرجى ربط بوت خارجي)"}

        except PhoneNumberUnoccupiedError:
            logger.info(f"[Layer 3] Unoccupied error. Phone is Not Registered. (Phone: {phone})")
            is_success = True
            return {"status": "NO_SESSION", "phone": phone, "status_text": "🆕 غير مسجل"}

        except PhoneNumberBannedError:
            logger.info(f"[Layer 3] Banned error. Phone is Banned. (Phone: {phone})")
            is_success = True
            return {"status": "BANNED", "phone": phone, "status_text": "📵 مـحـظـور"}

        except PhoneNumberInvalidError:
            logger.info(f"[Layer 3] Invalid phone. Phone is Not Registered. (Phone: {phone})")
            is_success = True
            return {"status": "NO_SESSION", "phone": phone, "status_text": "🆕 غير مسجل"}

        except FloodWaitError as e:
            await flood_manager.set_flood(account["id"], e.seconds)
            logger.warning(f"[Layer 3] FloodWait: {e.seconds} seconds on checker.")
            is_flood = True
            return {"status": "FLOOD_WAIT", "seconds": e.seconds, "phone": phone, "status_text": f"🚫 حظر مؤقت {e.seconds} ثانية"}

        except SessionPasswordNeededError:
            logger.info(f"[Layer 3] Session password needed! Phone is Registered. (Phone: {phone})")
            is_success = True
            return {"status": "HAS_SESSION", "phone": phone, "status_text": "⚠️ الرقم لديه جلسة"}

        except PhoneMigrateError as e:
            logger.info(f"[Layer 3] PhoneMigrateError to DC {e.new_dc}. Re-routing...")
            try:
                await client._switch_dc(e.new_dc)
                await asyncio.sleep(0.5)
                # Re-run Layer 3 (Recursive call or retry block)
                # For simplicity, we just return error to let it be retried by the system later or we can re-throw
                return {"status": "ERROR", "phone": phone, "status_text": f"🔄 انتقال لـ DC {e.new_dc} - سيتم فحصه مجدداً"}
            except Exception as migrate_err:
                return {"status": "ERROR", "phone": phone, "status_text": f"❌ فشل الانتقال لـ DC {e.new_dc}"}

        except Exception as e:
            error_str = str(e).upper()
            logger.error(f"[Layer 3] Unexpected exception for {phone}: {e}")
            if any(kw in error_str for kw in ["UNOCCUPIED", "NO USER", "NOT FOUND", "NOT_FOUND"]):
                return {"status": "NO_SESSION", "phone": phone, "status_text": "🆕 غير مسجل"}
            if "BANNED" in error_str:
                return {"status": "BANNED", "phone": phone, "status_text": "📵 مـحـظـور"}
            if "AUTH_KEY" in error_str:
                await account_manager.disable_account(account["id"])
                return {"status": "ACCOUNT_DISABLED", "phone": phone, "status_text": "❌ حساب الفاحص تالف"}
            return {"status": "ERROR", "phone": phone, "status_text": f"⚙️ خطأ نظام: {e}"}
"""

content = re.sub(pattern, replacement, content, flags=re.DOTALL)

with open('telegram_checker/checker.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("checker.py has been modified successfully.")
