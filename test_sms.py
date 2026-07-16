import re

with open('main_bot.py', 'r', encoding='utf-8') as f:
    content = f.read()

new_command = """
async def test_sms_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from config import ADMIN_ID
    if ADMIN_ID == 0 or update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("استخدم الأمر هكذا:\\n`/test_sms +123456789`", parse_mode="Markdown")
        return
    phone = context.args[0]
    await update.message.reply_text(f"⏳ جاري تجربة إجبار تيليجرام على إرسال SMS للرقم {phone}...")
    
    import database as db
    from telegram_checker.account_manager import account_manager
    from telegram_checker.client_manager import telegram_client_manager
    from telethon import functions, types
    import asyncio

    account = await account_manager.get_available_account()
    if not account:
        await update.message.reply_text("❌ لا يوجد حساب فاحص متاح.")
        return

    try:
        client = await telegram_client_manager.get_client(account)
        if not client.is_connected():
            await client.connect()
        
        await update.message.reply_text(f"✅ تم الاتصال بحساب الفاحص ID: {account['id']}.\\nجاري إرسال SendCodeRequest...")
        result = await client(functions.auth.SendCodeRequest(
            phone_number=phone,
            api_id=int(account["api_id"]),
            api_hash=account["api_hash"],
            settings=types.CodeSettings(allow_flashcall=False, current_number=True, allow_app_hash=True)
        ))
        
        await update.message.reply_text(f"✅ نتيجة SendCodeRequest الأولى:\\nالنوع: `{type(result.type).__name__}`\\n\\n⏳ ننتظر 3 ثواني ثم نطلب إرسال SMS (ResendCode)...", parse_mode="Markdown")
        
        await asyncio.sleep(3)
        
        resend_result = await client(functions.auth.ResendCodeRequest(
            phone_number=phone,
            phone_code_hash=result.phone_code_hash
        ))
        
        await update.message.reply_text(f"🔥 نتيجة إرسال SMS (ResendCodeRequest):\\nالنوع: `{type(resend_result.type).__name__}`\\n\\nنجحت التجربة في كشف الرد!", parse_mode="Markdown")
        
        try:
            await client(functions.auth.CancelCodeRequest(
                phone_number=phone,
                phone_code_hash=resend_result.phone_code_hash
            ))
        except Exception:
            pass
        
    except Exception as e:
        await update.message.reply_text(f"❌ فشلت التجربة (وهذا المتوقع من حماية تيليجرام):\\nالخطأ: `{type(e).__name__} - {e}`", parse_mode="Markdown")

"""

if "def test_sms_command" not in content:
    content = content.replace("async def admin_command", new_command + "\nasync def admin_command")

    handler_code = 'application.add_handler(CommandHandler("test_sms", test_sms_command))'
    if handler_code not in content:
        content = content.replace('application.add_handler(CommandHandler("admin", admin_command))', 
                                'application.add_handler(CommandHandler("admin", admin_command))\n    ' + handler_code)

    with open('main_bot.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print("test_sms_command added to main_bot.py")
else:
    print("test_sms_command already exists")

