import asyncio
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes, CallbackContext
from telegram.constants import ParseMode
import database as db
from durian_api import DurianAPI

from telegram_checker.checker import telegram_checker 

logger = logging.getLogger(__name__)

# [COUNTRY_MAP, ALL_COUNTRIES, COUNTRY_INFO - تم اختصارهم هنا لتوفير المساحة، لكنهم سيبقون كما هم في الملف الحقيقي]
# [تم الحفاظ على المتغيرات العالمية]
repeat_tracker = {}
bot_owner_id = None
MAX_CONCURRENT_REQUESTS = 2
semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

# ==================== 1. القائمة الرئيسية ====================
async def start_user_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "🔰 مرحباً بك في بوت صيد الأرقام 🔰\n\nاختر أحد الخيارات أدناه للبدء:"
    keyboard = [
        [InlineKeyboardButton("‹ ايقاف الصيد ›", callback_data="stop_hunting"), InlineKeyboardButton("‹ تشغيل الصيد ›", callback_data="start_hunting")],
        [InlineKeyboardButton("‹ إدارة الدول ›", callback_data="manage_countries"), InlineKeyboardButton("‹ اضافه دوله ›", callback_data="add_country_page_0")],
        [InlineKeyboardButton("‹ اعدادات ›", callback_data="bot_settings")],
        [InlineKeyboardButton("‹ احصائيات عمليات الشراء الناجحه ›", callback_data="purchase_stats")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.message:
        await update.message.reply_text(text, reply_markup=reply_markup)
    elif update.callback_query:
        await update.callback_query.message.edit_text(text, reply_markup=reply_markup)

# ==================== 2. قائمة الإعدادات ====================
async def show_settings(update: Update, user_id: int):
    channel = await db.get_hunting_channel(user_id)
    channel_status = f"✅ مربوطة ({channel})" if channel else "❌ غير مضافة"
    text = (
        f"⚙️ **قائمة الإعدادات:**\n\n"
        f"قناة الصيد الحالية: {channel_status}\n\n"
        f"قم بتعيين الإعدادات الأساسية للبوت قبل البدء في الصيد"
    )
    keyboard = [
        [InlineKeyboardButton("‹ إضافة قناة الصيد ✅ ›", callback_data="add_hunting_channel")],
        [InlineKeyboardButton("‹ إدارة الحسابات ›", callback_data="manage_accounts")],
        [InlineKeyboardButton("‹ رجوع ›", callback_data="main_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.message.edit_text(text, reply_markup=reply_markup)

# ==================== 3. إدارة الحسابات ====================
async def show_manage_accounts(update: Update, user_id: int):
    try:
        accounts = await db.get_all_site_accounts(user_id)
        plan = await db.get_user_plan(user_id)
        max_accounts = int(plan)
        if not accounts:
            text = "👤 **إدارة الحسابات:**\n\nلا توجد حسابات مضافة. أضف حسابًا للبدء."
            keyboard = [
                [InlineKeyboardButton("➕ إضافة حساب جديد", callback_data="add_new_site_account")],
                [InlineKeyboardButton("‹ رجوع ›", callback_data="bot_settings")]
            ]
        else:
            text = f"👤 **إدارة الحسابات (الحد الأقصى: {max_accounts} حسابات):**\n\nاختر الحساب الذي تريد إدارته:"
            keyboard = []
            for acc_id, username, api_key, is_active in accounts:
                status_icon = "🟢 مفعل" if is_active else "🔴 معطل"
                btn_text = f"{status_icon} - {username}"
                keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"account_detail_{acc_id}")])
            if len(accounts) < max_accounts:
                keyboard.append([InlineKeyboardButton("➕ إضافة حساب جديد", callback_data="add_new_site_account")])
            keyboard.append([InlineKeyboardButton("‹ رجوع ›", callback_data="bot_settings")])

        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.callback_query.message.edit_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error in show_manage_accounts: {e}")
        await update.callback_query.answer("❌ حدث خطأ أثناء تحميل الحسابات.", show_alert=True)
        
async def show_account_detail(update: Update, user_id: int, account_id: int):
    accounts = await db.get_all_site_accounts(user_id)
    acc = None
    for a in accounts:
        if a[0] == account_id:
            acc = a
            break
    if not acc:
        await update.callback_query.answer("الحساب غير موجود.", show_alert=True)
        return

    acc_id, username, api_key, is_active = acc
    status_text = "🟢 مفعل" if is_active else "🔴 معطل"
    text = (
        f"👤 **تفاصيل الحساب:**\n\n"
        f"📌 اسم المستخدم: `{username}`\n"
        f"🔑 API Key: `{api_key[:10]}...`\n"
        f"📊 الحالة: {status_text}\n\n"
        f"اختر الإجراء المطلوب:"
    )
    keyboard = [
        [InlineKeyboardButton("تفعيل ✅" if not is_active else "تعطيل ❌", callback_data=f"toggle_site_{acc_id}")],
        [InlineKeyboardButton("🗑️ حذف الحساب", callback_data=f"delete_site_{acc_id}")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="manage_accounts")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.message.edit_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    
# ==================== 4. معالجة الإدخالات النصية ====================
async def handle_user_inputs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user:
        return
    user_id = update.effective_user.id
    text = update.message.text.strip()

    if context.user_data.get("waiting_for_channel_id"):
        context.user_data.pop("waiting_for_channel_id", None)
        await db.save_hunting_channel(user_id, text)
        keyboard = [[InlineKeyboardButton("⬅️ العودة للإعدادات", callback_data="bot_settings")]]
        await update.message.reply_text("✅ تم ربط قناة الصيد بنجاح!", reply_markup=InlineKeyboardMarkup(keyboard))
        return
    elif context.user_data.get("waiting_for_username"):
        context.user_data["temp_username"] = text
        context.user_data.pop("waiting_for_username", None)
        context.user_data["waiting_for_apikey"] = True
        await update.message.reply_text("🔑 ممتاز، الآن أرسل الـ API Key:")
        return
    elif context.user_data.get("waiting_for_apikey"):
        username = context.user_data.get("temp_username")
        api_key = text
        context.user_data.pop("waiting_for_apikey", None)
        context.user_data.pop("temp_username", None)
        await db.save_site_account_v2(user_id, username, api_key)
        keyboard = [[InlineKeyboardButton("⬅️ العودة لإدارة الحسابات", callback_data="manage_accounts")]]
        await update.message.reply_text("✅ تم إضافة الحساب بنجاح!", reply_markup=InlineKeyboardMarkup(keyboard))
        return

# ==================== 5. معالج الأحداث والأزرار ====================
async def user_bot_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global bot_owner_id
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data
    logger.warning(f"CALLBACK RECEIVED: user={user_id}, data={data}")

    if data == "main_menu":
        await start_user_bot(update, context)
    elif data == "bot_settings":
        await show_settings(update, user_id)
    elif data == "manage_accounts":
        await show_manage_accounts(update, user_id)
        return
    elif data.startswith("account_detail_"):
        acc_id = int(data.split("_")[2])
        await show_account_detail(update, user_id, acc_id)
        return
    elif data.startswith("toggle_site_"):
        acc_id = int(data.split("_")[2])
        try:
            await db.toggle_site_account(user_id, acc_id)
        except Exception as e:
            await query.answer(f"❌ خطأ: {e}", show_alert=True)
            return
        await query.answer("✅ تم تبديل الحالة", show_alert=False)
        await show_account_detail(update, user_id, acc_id)
        return
    elif data.startswith("delete_site_"):
        acc_id = int(data.split("_")[2])
        await db.delete_site_account(user_id, acc_id)
        await show_manage_accounts(update, user_id)
        return
    elif data == "start_hunting":
        active_accounts = await db.get_active_site_accounts(user_id)
        channel = await db.get_hunting_channel(user_id)
        countries = await db.get_user_countries(user_id)
        if not active_accounts or not channel or not countries:
            await query.message.reply_text("❌ تأكد من إضافة حسابات وقناة ودول للصيد.")
            return
        bot_owner_id = user_id
        context.job_queue.run_repeating(check_and_hunt_numbers, interval=4, first=1, user_id=user_id, name=f"hunt_{user_id}")
        await db.set_hunting_status(user_id, 1)
        await query.answer("🚀 تم تشغيل الصيد!", show_alert=True)
    elif data == "stop_hunting":
        await db.set_hunting_status(user_id, 0)
        current_jobs = context.job_queue.get_jobs_by_name(f"hunt_{user_id}")
        for job in current_jobs:
            job.schedule_removal()
        await query.answer("🛑 تم إيقاف الصيد.", show_alert=True)
    elif data.startswith(("code_", "unban_", "cancel_", "rate_", "weak_")):
        parts = data.split("_")
        action = parts[0]
        
        # تحديد الصيغة (هل هي جزأين phone فقط، أم 3 أجزاء username_phone)
        if len(parts) == 2:
            phone = parts[1]
            # في الفحص التلقائي، لا يتوفر username في البيانات، نستخدم أول حساب متاح للمستخدم
            owner_id = bot_owner_id if bot_owner_id is not None else user_id
            accounts = await db.get_all_site_accounts(owner_id)
            if not accounts:
                await safe_answer(query, "❌ لا توجد حسابات فحص متاحة!", show_alert=True)
                return
            username = accounts[0][1]
            api_key = accounts[0][2]
        elif len(parts) >= 3:
            username = parts[1]
            phone = parts[2]
            owner_id = bot_owner_id if bot_owner_id is not None else user_id
            accounts = await db.get_all_site_accounts(owner_id)
            api_key = None
            for acc_id, acc_username, acc_api_key, _ in accounts:
                if acc_username == username:
                    api_key = acc_api_key
                    break
        else:
            await safe_answer(query, "⚠️ صيغة الزر غير مدعومة...", show_alert=True)
            return

        if not api_key:
            await safe_answer(query, "❌ الحساب المرتبط غير موجود!", show_alert=True)
            return

        if action == "code":
            await safe_answer(query, "⏳ جاري طلب الكود يرجى الانتظار", show_alert=True)
            try:
                sms_res = await DurianAPI.get_sms(username, api_key, phone)
                if sms_res["status"] == "success":
                    # بناء النص الجديد
                    updated_text = (
                        f"<b>🔰 تـم شـراء رقـم جـديـد مـن DurianRCS 🔰</b>\n\n"
                        f"<b>    - الـرقـــــم : <code>{phone}</code></b>\n"
                        f"<b>    - الـحـالـة : ✅ تـم الـوصـول</b>\n"
                        f"<b>    - الــكـــود : {sms_res['sms']}</b>"
                    )
                    try:
                        await query.message.edit_text(text=updated_text, reply_markup=None, parse_mode=ParseMode.HTML)
                    except Exception:
                        pass
                else:
                    await safe_answer(query, "❌ فشل جلب الكود.", show_alert=True)
            except Exception as e:
                logger.error(f"Error fetching SMS for {phone}: {e}")
                await safe_answer(query, "❌ فشل جلب الكود.", show_alert=True)
        elif action == "cancel":
            try:
                success = await DurianAPI.cancel_number(username, api_key, phone)
                if success:
                    try:
                        await query.message.delete()
                    except Exception:
                        pass
                    await safe_answer(query, "🗑️ تم إلغاء الرقم بنجاح.", show_alert=True)
                else:
                    await safe_answer(query, "❌ فشل إلغاء الرقم.", show_alert=True)
            except Exception as e:
                logger.error(f"Error cancelling number {phone}: {e}")
                await safe_answer(query, "❌ خطأ في الإلغاء", show_alert=True)

# ==================== 7. دالة الصيد والضخ ====================
async def check_and_hunt_numbers(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    user_id = job.user_id
    active_accounts = await db.get_active_site_accounts(user_id)
    channel = await db.get_hunting_channel(user_id)
    countries = await db.get_user_countries(user_id)
    # ... (باقي المنطق مع await)
