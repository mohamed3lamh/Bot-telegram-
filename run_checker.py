import asyncio
import os
import sys
import database as db
from telegram_checker.login_manager import login_manager

# ⚠️ ضع بيانات الحساب الفاحص هنا بدقة بين علامات التنصيص ""
PHONE = "+967775435959"         # رقم الهاتف مع رمز الدولة
API_ID = 1234567                # الـ ID الخاص بك
API_HASH = "your_api_hash"      # الـ HASH الخاص بك

async def main():
    print(f"🚀 جاري تسجيل الدخول للفاحص: {PHONE}")
    
    # محاولة تسجيل الدخول
    try:
        await login_manager.send_code(PHONE, API_ID, API_HASH)
        code = input("📥 أدخل الكود المرسل لتيليجرام: ")
        result = await login_manager.verify_code(PHONE, code)
        
        if result["status"] == "PASSWORD_REQUIRED":
            password = input("🔐 الحساب محمي بكلمة سر، أدخلها: ")
            result = await login_manager.verify_password(PHONE, password)
            
        if result["status"] == "SUCCESS":
            print(f"✅ تم تسجيل دخول الفاحص بنجاح: {result['name']}")
            print(f"📌 تم حفظ الجلسة في قاعدة البيانات.")
        else:
            print(f"❌ فشل تسجيل الدخول: {result}")
            
    except Exception as e:
        print(f"\n❌ حدث خطأ أثناء التحقق من الكود: {e}")
    finally:
        await login_manager.cleanup()

if __name__ == '__main__':
    asyncio.run(main())
