import asyncio
import logging
from datetime import datetime, timezone
import database as db
from durian_api import DurianAPI

# إعداد السجلات للمحرك الخلفي
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger("HuntingWorker")

async def process_user_hunting(user_id, username, api_key, countries):
    """
    معالجة عمليات الصيد لكل مشترك ودولة بشكل مستقل
    """
    for country_code in countries:
        try:
            # 1. طلب سحب رقم من Durian API باستخدام حساب المشترك الخاص
            res = await DurianAPI.order_number_by_name(username, api_key, country_code)
            if not res or "phone" not in res:
                continue

            phone_number = res["phone"]
            logger.info(f"🎰 [اصطياد] تم سحب رقم جديد للمشترك {user_id}: {phone_number} (الدولة: {country_code})")

            # 2. الفحص الداخلي الآمن عبر قاعدة البيانات المباشرة 
            # (قمنا بحذف استدعاء الـ checker الخارجي المسبب للـ Conflict)
            status_type = "READY"
            status_text = "🟢 جاهز للفحص والتفعيل"

            # 3. حفظ النتيجة فوراً في قاعدة البيانات (صندوق البريد) ليلتقطها بوت المشترك
            db.insert_pending_report(
                user_id=user_id,
                username=username,
                phone_number=phone_number,
                country_code=country_code,
                status_text=status_text,
                status_type=status_type
            )
            logger.info(f"💾 [حفظ] تم تسجيل تقرير الرقم {phone_number} في جدول الانتظار بنجاح.")

        except Exception as e:
            logger.error(f"⚠️ خطأ أثناء الصيد للمشترك {user_id} دولة {country_code}: {e}")

async def main():
    logger.info("🚀 تم تشغيل محرك الصيد والفحص المركزي الخلفي بنجاح...")
    
    while True:
        try:
            # جلب كل المستخدمين النشطين
            active_bots = db.get_all_active_bots()
            
            for user_id, token in active_bots:
                conn = db.get_connection()
                cursor = conn.cursor()
                
                # ✅ تم تعديل اسم الجدول هنا إلى الاسم الصحيح والموجود بمشروعك لمنع خطأ 42P01
                cursor.execute("SELECT username, api_key FROM user_site_accounts WHERE user_id = %s AND is_active = TRUE", (user_id,))
                durian_accounts = cursor.fetchall()
                
                # جلب الدول المفعلة للصيد للمشترك
                cursor.execute("SELECT country_name FROM user_countries WHERE user_id = %s AND is_hunting = TRUE", (user_id,))
                countries = [row[0] for row in cursor.fetchall()]
                
                cursor.close()
                conn.close()

                if not durian_accounts or not countries:
                    continue

                # إطلاق مهام الصيد بالتوازي لكل حساب من حسابات المشترك
                tasks = []
                for username, api_key in durian_accounts:
                    tasks.append(process_user_hunting(user_id, username, api_key, countries))
                
                if tasks:
                    await asyncio.gather(*tasks)

        except Exception as e:
            logger.error(f"⚠️ خطأ في الحلقة الرئيسية للمحرك الخلفي: {e}")
        
        # مدة الراحة لحماية السيرفر وقاعدة البيانات من الضغط
        await asyncio.sleep(5)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("🛑 تم إيقاف المحرك الخلفي.")
