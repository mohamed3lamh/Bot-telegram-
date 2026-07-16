import re

with open('main_bot.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Add button to show_admin_panel
pattern_btn = r"InlineKeyboardButton\(\"🤖 ربط بوت فحص خارجي\", callback_data=\"adm_checker_bot\"\)\n\s*\],"
replacement_btn = """InlineKeyboardButton("🤖 ربط بوت فحص خارجي", callback_data="adm_checker_bot")
        ],
        [
            InlineKeyboardButton("👑 تخصيص حساب المدير (Layer 4)", callback_data="adm_set_manager_acc")
        ],"""
content = re.sub(pattern_btn, replacement_btn, content)

# 2. Add callback handler in button_handler
pattern_cb = r"elif query\.data == \"adm_checker_bot\":\n\s*context\.user_data\[\"admin_action\"\] = \"set_checker_bot\"\n\s*await query\.message\.reply_text\([\s\S]*?return\n"
replacement_cb = """elif query.data == "adm_checker_bot":
            context.user_data["admin_action"] = "set_checker_bot"
            await query.message.reply_text(
                "🤖 **نظام ربط بوت فحص خارجي**\\n\\n"
                "الرجاء إرسال معرف البوت. مثال:\\n"
                "`SessionCheckerReBoT`\\n\\n"
                "لإلغاء التفعيل أرسل: `off`",
                parse_mode="Markdown"
            )
            return
        elif query.data == "adm_set_manager_acc":
            context.user_data["admin_action"] = "set_manager_acc"
            # Get all accounts to show IDs
            try:
                import database as db
                from telegram_checker.account_manager import account_manager
                accounts = await account_manager.get_all_accounts()
                msg = "👑 **تخصيص حساب المدير للطبقة الرابعة**\\n\\n"
                msg += "هذا الحساب سيتولى حصرياً مراسلة البوت الخارجي.\\n"
                msg += "الحسابات المتاحة:\\n"
                for acc in accounts:
                    msg += f"ID: `{acc['id']}` - Number: {acc['phone_number']}\\n"
                msg += "\\nالرجاء إرسال ID الحساب المطلوب كمدير، أو أرسل `off` لتعطيل الميزة وجعل أي حساب يراسل البوت."
                await query.message.reply_text(msg, parse_mode="Markdown")
            except Exception as e:
                await query.message.reply_text(f"خطأ: {e}")
            return
"""
content = re.sub(pattern_cb, replacement_cb, content)

# 3. Add text handler in handle_admin_message
pattern_txt = r"elif action == \"set_checker_bot\":[\s\S]*?return\n"
replacement_txt = """elif action == "set_checker_bot":
            bot_username = text.replace("@", "").strip()
            if bot_username.lower() == "off":
                await asyncio.to_thread(db.set_setting, "checker_bot_username", "")
                await update.message.reply_text("❌ تم تعطيل الربط مع بوت الفحص الخارجي.")
            else:
                await asyncio.to_thread(db.set_setting, "checker_bot_username", bot_username)
                await update.message.reply_text(f"✅ تم تفعيل الربط مع البوت: @{bot_username}")
            context.user_data.pop("admin_action", None)
            return
        elif action == "set_manager_acc":
            val = text.strip()
            if val.lower() == "off":
                await asyncio.to_thread(db.set_setting, "external_checker_account_id", "")
                await update.message.reply_text("❌ تم تعطيل ميزة الحساب المدير. سيتم المراسلة من أي حساب فاحص متاح.")
            else:
                try:
                    acc_id = int(val)
                    await asyncio.to_thread(db.set_setting, "external_checker_account_id", str(acc_id))
                    await update.message.reply_text(f"✅ تم تخصيص الحساب رقم `{acc_id}` ليكون حساب المدير (Layer 4).", parse_mode="Markdown")
                except ValueError:
                    await update.message.reply_text("❌ يرجى إرسال أرقام فقط (ID الحساب).")
            context.user_data.pop("admin_action", None)
            return
"""
content = re.sub(pattern_txt, replacement_txt, content)

with open('main_bot.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("main_bot.py updated.")

