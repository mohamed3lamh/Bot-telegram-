from telegram_checker.checker import telegram_checker
import asyncio

async def check_phone(phone):
    # الحصول على حساب فحص متاح
    account = await telegram_checker.get_available_account()
    if not account:
        return "⚪️ لا يوجد حساب فحص متاح"
        
    result = await telegram_checker.check_phone(account, phone)
    return result.get("status_text", "⚪️ غير معروف")
