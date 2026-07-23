import asyncio
import os
import shutil
import uuid
import sys
import json

# التأكد من إدراج المجلد الحالي في مسارات البحث
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from telegram_checker.backend.tdlib_binding.core import TDLibClient

# ==========================================
# ضع هنا بيانات الـ API الخاصة بك للاختبار
# ==========================================
API_ID = 1234567  # استبدله بـ API ID الحقيقي
API_HASH = "your_api_hash"  # استبدله بـ API HASH الحقيقي
LIB_PATH = "libtdjson.so" # مسار مكتبة TDLib في السيرفر الخاص بك

async def main():
    if len(sys.argv) < 2:
        print("Usage: python prototype_layer3.py <phone_number>")
        return

    phone_number = sys.argv[1]
    session_dir = f"temp_tdlib_session_{uuid.uuid4().hex[:8]}"

    print(f"\n[*] بدء اختبار إنشاء عميل TDLib مؤقت للرقم: {phone_number}")
    print(f"[*] مسار الجلسة المؤقتة: {session_dir}")

    try:
        client = TDLibClient(lib_path=LIB_PATH)
    except Exception as e:
        print(f"\n[X] فشل تحميل مكتبة TDLib. تأكد من وجود ملف {LIB_PATH} في النظام.")
        print(f"الخطأ: {e}")
        return
    
    # قائمة لتخزين التحديثات التي تصل من العميل (لمتابعة الـ State Machine)
    updates = []
    def on_update(data):
        updates.append(data)
        
    client.start(update_handler=on_update)
    
    try:
        # 1. إرسال إعدادات TDLib الأولية
        print("[*] جاري ضبط الإعدادات (setTdlibParameters)...")
        res = await client.send({
            "@type": "setTdlibParameters",
            "use_test_dc": False,
            "database_directory": session_dir,
            "files_directory": session_dir,
            "database_encryption_key": b"".hex(),
            "use_file_database": True, 
            "use_chat_info_database": False,
            "use_message_database": False,
            "use_secret_chats": False,
            "api_id": API_ID,
            "api_hash": API_HASH,
            "system_language_code": "en",
            "device_model": "Prototype Checker",
            "application_version": "1.0",
        })
        
        # 2. انتظار وصول حالة WaitPhoneNumber
        print("[*] جاري انتظار حالة العميل ليكون جاهزاً لإرسال الكود...")
        await asyncio.sleep(1.5) 
        
        # 3. إرسال طلب الكود (setAuthenticationPhoneNumber)
        print(f"[*] يتم الآن إرسال طلب الكود (setAuthenticationPhoneNumber) للرقم {phone_number}...")
        res = await client.send({
            "@type": "setAuthenticationPhoneNumber",
            "phone_number": phone_number,
            "settings": {
                "@type": "phoneNumberAuthenticationSettings",
                "allow_flash_call": False,
                "is_current_phone_number": False,
                "allow_sms_retriever_api": False
            }
        })
        
        # 4. طباعة النتيجة الفعلية القادمة من تيليجرام
        print("\n================== النتيجة ==================")
        if res.get("@type") == "ok":
            print("[!] تم استلام الاستجابة: 'ok'")
            print("[!] الاستنتاج: تم إرسال الكود بنجاح! (الرقم مسجل - HAS_SESSION)")
        elif res.get("@type") == "error":
            print(f"[!] تم استلام الاستجابة: 'error'")
            print(f"    - كود الخطأ: {res.get('code')}")
            print(f"    - رسالة الخطأ: {res.get('message')}")
            
            msg = res.get('message', '')
            if "PHONE_NUMBER_BANNED" in msg:
                print("    -> الاستنتاج: الرقم محظور (BANNED)")
            elif "PHONE_NUMBER_UNOCCUPIED" in msg or "PHONE_NUMBER_INVALID" in msg:
                print("    -> الاستنتاج: الرقم غير مسجل (NO_SESSION)")
            else:
                print("    -> الاستنتاج: خطأ آخر (يُرجى التحليل)")
        
        print("=============================================")
        
    except Exception as e:
        print(f"\n[X] حدث خطأ غير متوقع أثناء الاختبار: {e}")
        
    finally:
        print("\n[*] جاري تدمير العميل المؤقت كلياً ومسح الجلسة (لإلغاء الطلب والتخفي)...")
        client.stop()
        if os.path.exists(session_dir):
            shutil.rmtree(session_dir)
        print("[+] تم التدمير والتنظيف بنجاح. انتهت الدورة.")

if __name__ == "__main__":
    asyncio.run(main())
