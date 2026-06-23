import asyncio
import os
import sys
import database as db
from telegram_checker.login_manager import login_manager

# ⚠️ ضع بيانات الحساب الفاحص هنا بدقة بين علامات التنصيص ""
PHONE = "+967775435959"         # رقم الهاتف مع رمز الدولة
API_ID = "32507194"            # الـ API ID الخاص بحسابك
API_HASH = "885aed505372434518892a7e0f7fccc1"   # الـ API HASH الخاص بحسابك

# ⚠️ إذا وصلك الكود وتريد تأكيده، ضعه هنا بين علامات التنصيص، وإذا لم يصلك بعد اتركه فارغاً ""
VERIFICATION_CODE = "78094" 

# ⚠️ إذا كان الحساب محمي بالتحقق بخطوتين، ضع الباسورد هنا، وإذا لم يكن محمي اتركه فارغاً ""
TWO_FACTOR_PASSWORD = ""

async def main():
    print("\n=============================================")
    print("🚀 بدء سكربت حقن الحساب الفاحص في قاعدة بيانات PostgreSQL...")
    print("=============================================\n")
    
    # تأكد من تهيئة قاعدة البيانات
    try:
        db.init_db()
    except Exception as e:
        print(f"❌ خطأ في تهيئة قاعدة البيانات: {e}")
        return

    if not VERIFICATION_CODE:
        # المرحلة الأولى: إرسال الكود
        print(f"⏳ جاري الاتصال بتليجرام لإرسال كود التحقق إلى الرقم: {PHONE} ...")
        try:
            await login_manager.send_code(PHONE, API_ID, API_HASH)
            print("\n✅ تم إرسال كود التحقق بنجاح إلى حسابك في تليجرام!")
            print("📱 افتح تليجرام، خذ الكود، ثم افتح هذا الملف مجدداً وضعه في خانة VERIFICATION_CODE واعمل حفظ (Commit).")
        except Exception as e:
            print(f"\n❌ حدث خطأ أثناء إرسال الكود: {e}")
        finally:
            await login_manager.cleanup()
    else:
        # المرحلة الثانية: تأكيد الكود وحفظ الجلسة
        print(f"⏳ جاري التحقق من الكود {VERIFICATION_CODE} للحساب...")
        try:
            result = await login_manager.verify_code(PHONE, VERIFICATION_CODE)
            
            if result.get("status") == "PASSWORD_REQUIRED":
                if not TWO_FACTOR_PASSWORD:
                    print("\n🔒 الحساب محمي بالتحقق بخطوتين! يرجى كتابة الباسورد في خانة TWO_FACTOR_PASSWORD وحفظ الملف مجدداً.")
                    await login_manager.cleanup()
                    return
                else:
                    print("⏳ جاري التحقق من كلمة المرور بخطوتين...")
                    result = await login_manager.verify_password(PHONE, TWO_FACTOR_PASSWORD)
            
            if result.get("status") == "SUCCESS":
                print(f"\n🎉 ✅ تم بنجاح تفعيل الحساب الفاحص وحفظه في قاعدة البيانات المشتركة!")
                print(f"👤 اسم الحساب: {result.get('name')}")
            else:
                print(f"\n❌ فشل التحقق، النتيجة: {result}")
                
        except Exception as e:
            print(f"\n❌ حدث خطأ أثناء التحقق من الكود: {e}")
        finally:
            await login_manager.cleanup()

if __name__ == '__main__':
    asyncio.run(main())
