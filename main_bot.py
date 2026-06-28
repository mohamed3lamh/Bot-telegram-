import os
import asyncio
import logging
from datetime import datetime, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes, ConversationHandler
from telegram.request import HTTPXRequest
import database as db
from bot_manager import bot_manager
from telegram_checker.login_manager import login_manager

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

MAIN_TOKEN = os.getenv("MAIN_BOT_TOKEN")
try:
    ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
except Exception:
    ADMIN_ID = 0

PHONE, CODE, PASSWORD = range(3)

async def show_welcome(update: Update, user_name: str):
    text = (
        f"😁👋 ❛ ≽ السلام عليكم ورحمة الله وبركاته ≼\n\n"
        f"👤 ❛ ≽ حياك الله يا {user_name} 🎊، أهلاً وسهلاً ومرحباً بك. ≼\n\n"
        f"🤖 ❛ ≽ البوت المميز والحصري في تقديم خدمة صيد الأرقام مع فك الحظر التلقائي عن الأرقام. ≼\n\n"
        f"📮 ❛ ≽ كل ما عليك فقط أن تشترك في البوت لتبدأ رحلت صيد الأرقام العالمية والدولية 📱 ≼\n\n"
        f"ماذا تنتظر...!؟\n≽ ≽ ≽ اضغط هنا وابدأ 🔻"
    )
    keyboard = [[InlineKeyboardButton("اشترك الان", callback_data="subscribe")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.message:
        await update.message.reply_text(text, reply_markup=reply_markup)
    elif update.callback_query:
        await update.callback_query.message.edit_text(text, reply_markup=reply_markup)

def get_correct_table_name():
    return "user_bots"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name
    db_data = db.get_bot(user_id)
    if db_data and len(db_data) >= 4:
        if db_data[3] == 1:
            await update.message.reply_text("❌ عذراً، تم إيقاف حسابك وحظرك من استخدام المنصة من قبل الإدارة.")
            return
        # تحقق من الاشتراك
        expires_at = db_data[2]
        if expires_at:
            if isinstance(expires_at, str):
                expires_at = datetime.fromisoformat(expires_at.replace("Z", ""))
            if expires_at.replace(tzinfo=None) > datetime.now(timezone.utc).replace(tzinfo=None):
                await show_dashboard(update, user_id, user_name)
                return
    # لا يوجد اشتراك ساري
    await show_welcome(update, user_name)

async def handle_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name
    text = update.message.text.strip()

    if context.user_data.get("waiting_for_token"):
        context.user_data.pop("waiting_for_token", None)
        # التحقق من صحة التوكن
        is_valid = await bot_manager.validate_token(text)
        if not is_valid:
            await update.message.reply_text("❌ التوكن غير صالح! تأكد من الحصول عليه بشكل صحيح من @BotFather.")
            return
        try:
            await db.save_bot(user_id, text)
            await update.message.reply_text(
                f"✅ تم تحديث توكن البوت بنجاح\n\n"
                f"🔑 التوكن: <code>{text}</code>",
                parse_mode="HTML"
            )
        except Exception as e:
            await update.message.reply_text(f"❌ خطأ في قاعدة البيانات: {e}")
        return

    if ADMIN_ID != 0 and user_id == ADMIN_ID and context.user_data.get("admin_action") == "ticket_reply":
        ticket_id = context.user_data.get("replying_ticket_id")
        if ticket_id:
            reply_text = text
            db.reply_ticket(ticket_id, reply_text)
            db.log_activity(user_id, "رد على تذكرة", f"Ticket #{ticket_id}")
            await update.message.reply_text("✅ تم إرسال الرد وإغلاق التذكرة.")
            context.user_data.pop("admin_action", None)
            context.user_data.pop("replying_ticket_id", None)
            return

    # معالجة تعديل الإعدادات
    if ADMIN_ID != 0 and user_id == ADMIN_ID and context.user_data.get("admin_action") == "edit_setting":
        key = context.user_data.get("editing_setting_key")
        if key:
            db.set_setting(key, text)
            db.log_activity(user_id, "تعديل إعداد", f"{key} = {text}")
            await update.message.reply_text(f"✅ تم تحديث {key} إلى {text}")
            context.user_data.pop("admin_action", None)
            context.user_data.pop("editing_setting_key", None)
            return
            
    if ADMIN_ID != 0 and user_id == ADMIN_ID and context.user_data.get("admin_action"):
        action = context.user_data.get("admin_action")
        table_name = get_correct_table_name()
        if action == "add_days":
            try:
                target_id, value = text.split(" ")
                target_id = int(target_id)
                await db.add_days_to_user(target_id, int(value))
                db.log_activity(user_id, "إضافة أيام", f"للمستخدم {target_id} - {value} يوم")
                await update.message.reply_text(f"✅ تم إضافة {value} يوم للمستخدم `{target_id}` بنجاح.")
            except Exception:
                await update.message.reply_text("❌ صيغة خاطئة. يرجى إدخال: `المعرف القيمة` (مثال: `834033986 30`)")
            context.user_data.pop("admin_action", None)
            return
        elif action == "ban":
            try:
                target_id, value = text.split(" ")
                target_id = int(target_id)
                db.ban_user(target_id, int(value))
                db.log_activity(user_id, "حظر/إلغاء حظر", f"مستخدم {target_id} - حالة {value}")
                status_text = "حظر" if int(value) == 1 else "إلغاء حظر"
                await update.message.reply_text(f"✅ تم تعديل حالة المستخدم `{target_id}` إلى: **{status_text}**.")
            except Exception:
                await update.message.reply_text("❌ صيغة خاطئة. يرجى إدخال: `المعرف القيمة` (مثال للحظر: `834033986 1`)")
            context.user_data.pop("admin_action", None)
            return
        elif action == "delete_user":
            try:
                target_id = int(text)
                try:
                    await bot_manager.stop_bot(target_id)
                except Exception:
                    pass
                conn = db.get_connection()
                cursor = conn.cursor()
                cursor.execute(f"DELETE FROM {table_name} WHERE user_id = %s", (target_id,))
                conn.commit()
                db.log_activity(user_id, "حذف مستخدم", f"المستخدم {target_id}")
                cursor.close()
                conn.close()
                await update.message.reply_text(f"🗑️ تم حذف المستخدم `{target_id}` نهائياً من الجدول `{table_name}` وإيقاف خط السحب الخاص به.")
            except Exception as e:
                await update.message.reply_text(f"❌ فشل تنفيذ الحذف. الخطأ: {e}")
            context.user_data.pop("admin_action", None)
            return

    status_msg = await update.message.reply_text("⏳ جاري التحقق من صحة التوكن المرسل وحفظه...")
    is_valid = await bot_manager.validate_token(text)
    if not is_valid:
        await status_msg.edit_text("❌ التوكن غير صالح! تأكد من الحصول عليه بشكل صحيح من @BotFather.")
        return
    try:
        await db.save_bot(user_id, text)
        await status_msg.delete()
        await update.message.reply_text("✅ تم شحن وتحديث توكن البوت الجديد بنجاح!")
    except Exception as e:
        await status_msg.edit_text(f"❌ خطأ في قاعدة البيانات: {e}")
        return
    await show_dashboard(update, user_id, user_name)

async def show_dashboard(update: Update, user_id: int, user_name: str):
    days_left = "36 يوم"
    status = "⚪️ غير مربوط"
    try:
        db_data = db.get_bot(user_id)
        if db_data and len(db_data) >= 4:
            status = bot_manager.get_status(user_id)
            expires_at = db_data[2]
            if expires_at:
                if isinstance(expires_at, str):
                    expires_at = datetime.fromisoformat(expires_at.replace("Z", ""))
                delta = expires_at.replace(tzinfo=None) - datetime.now(timezone.utc).replace(tzinfo=None)
                days_left = f"{max(0, delta.days)} يوم"
    except Exception:
        days_left = "36 يوم"

    text = (
        f"👤 ⪪ حياك الله يا {user_name} 🦾، أهلاً وسهلاً ومرحباً بك.\n\n"
        f"🟢 ⪪ لديك اشتراك نشط، يمكنك هنا تشغيل وإيقاف البوت الخاص بك ⪪ {status}\n\n"
        f"⏰ ⪪ اشتراكك ⪪ {days_left} ⪪"
    )

    keyboard = [
        [InlineKeyboardButton("🔑 توكن البوت", callback_data="show_token_info")],
        [
            InlineKeyboardButton("إيقاف البوت ❌", callback_data="stop_bot"),
            InlineKeyboardButton("تشغيل البوت ✅", callback_data="run_bot")
        ],
        [InlineKeyboardButton("🔄 تجديد الإشتراك", callback_data="renew_subscription")],
        [
            InlineKeyboardButton("تواصل مع الدعم 🤙", callback_data="contact_support"),
            InlineKeyboardButton("بوت فك الحظر ❗️", callback_data="unban_bot")
        ]
    ]
    if ADMIN_ID != 0 and user_id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("👑 لوحة تحكم الإدارة 👑", callback_data="admin_panel")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.message:
        await update.message.reply_text(text, reply_markup=reply_markup)
    elif update.callback_query:
        try:
            await update.callback_query.message.edit_text(text, reply_markup=reply_markup)
        except Exception:
            pass

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if ADMIN_ID == 0 or update.effective_user.id != ADMIN_ID:
        return
    await show_admin_panel(update)

async def show_admin_panel(update: Update):
    try:
        total, active = db.get_stats()
    except Exception:
        total, active = 0, 0

    text = (
        f"👑 **لوحة تحكم المطور الفنية الشاملة** 👑\n\n"
        f"📊 **إحصائيات النظام الفورية:**\n"
        f"👥 إجمالي المستخدمين في النظام: {total}\n"
        f"🚀 البوتات الفرعية النشطة حالياً: {active}\n\n"
        f"⚙️ قم باختيار الإجراء المناسب لإدارة المشتركين والاشتراكات الشهرية:"
    )

    keyboard = [
        [
            InlineKeyboardButton("➕ شحن/تجديد الأيام", callback_data="adm_add_days"),
            InlineKeyboardButton("🚫 حظر / إلغاء حظر", callback_data="adm_ban")
        ],
        [
            InlineKeyboardButton("🗑️ حذف مستخدم نهائياً", callback_data="adm_delete_user"),
            InlineKeyboardButton("🆔 استخراج الـ IDs", callback_data="adm_get_ids")
        ],
        [
            InlineKeyboardButton("👥 إدارة المستخدمين", callback_data="adm_user_management"),
            InlineKeyboardButton("📋 سجل العمليات", callback_data="adm_activity_log")
        ],
        [
            InlineKeyboardButton("🎫 تذاكر الدعم", callback_data="adm_tickets"),
            InlineKeyboardButton("⚙️ إعدادات الأسعار", callback_data="adm_settings")
        ],
        [
            InlineKeyboardButton("⚙️ إضافة حساب فاحص", callback_data="adm_add_checker"),
            InlineKeyboardButton("👥 إدارة الحسابات الفاحصة", callback_data="adm_manage_checkers")
        ],
        [
            InlineKeyboardButton("⬅️ الواجهة الرئيسية", callback_data="main_menu")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.callback_query:
        await update.callback_query.message.edit_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)

# ---------- إدارة المستخدمين المتقدمة ----------
async def show_user_management(update: Update, page=0):
    query = update.callback_query
    per_page = 10
    offset = page * per_page
    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, token, is_active, expires_at, is_banned FROM user_bots ORDER BY user_id LIMIT %s OFFSET %s", (per_page, offset))
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
    except Exception as e:
        await query.answer(f"خطأ: {e}", show_alert=True)
        return

    if not rows:
        text = "لا يوجد مستخدمون."
        keyboard = [[InlineKeyboardButton("🔙 لوحة الإدارة", callback_data="admin_panel")]]
    else:
        text = "👥 **قائمة المستخدمين:**\n\n"
        keyboard = []
        for r in rows:
            uid, _, is_active, expires_at, is_banned = r
            status = "🟢 نشط" if is_active else "🔴 متوقف"
            if is_banned:
                status = "🚫 محظور"
            exp = "غير محدد"
            if expires_at:
                if isinstance(expires_at, str):
                    expires_at = datetime.fromisoformat(expires_at.replace("Z", ""))
                exp = expires_at.strftime("%Y-%m-%d")
            btn_text = f"ID: {uid} - {status} - {exp}"
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"user_detail_{uid}")])
        # أزرار التنقل بين الصفحات
        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"user_page_{page-1}"))
        # بفرض وجود المزيد (فحص بسيط)
        conn = db.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM user_bots")
        total = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        if offset + per_page < total:
            nav_row.append(InlineKeyboardButton("التالي ➡️", callback_data=f"user_page_{page+1}"))
        if nav_row:
            keyboard.append(nav_row)
        keyboard.append([InlineKeyboardButton("🔙 لوحة الإدارة", callback_data="admin_panel")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")

async def show_user_detail(update: Update, user_id: int):
    query = update.callback_query
    data = db.get_bot(user_id)
    if not data:
        await query.answer("المستخدم غير موجود", show_alert=True)
        return
    token, is_active, expires_at, is_banned = data
    status = "نشط" if is_active else "متوقف"
    banned = "نعم" if is_banned else "لا"
    exp_text = "غير محدد"
    if expires_at:
        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at.replace("Z", ""))
        exp_text = expires_at.strftime("%Y-%m-%d %H:%M")
    # عدد الحسابات المربوطة
    accounts = db.get_all_site_accounts(user_id)
    num_accounts = len(accounts)
    text = (
        f"👤 **معلومات المستخدم:**\n"
        f"🆔 المعرف: `{user_id}`\n"
        f"📌 الحالة: {status}\n"
        f"🚫 محظور: {banned}\n"
        f"⏰ انتهاء الاشتراك: {exp_text}\n"
        f"🗂 حسابات DurianRCS: {num_accounts}\n"
    )
    keyboard = [
        [InlineKeyboardButton("➕ إضافة أيام", callback_data=f"adm_add_days_{user_id}"),
         InlineKeyboardButton("🚫 حظر/إلغاء", callback_data=f"adm_ban_{user_id}")],
        [InlineKeyboardButton("🗑️ حذف المستخدم", callback_data=f"adm_delete_user_{user_id}")],
        [InlineKeyboardButton("🔙 عودة للقائمة", callback_data="adm_user_management")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")

# ---------- سجل العمليات ----------
async def show_activity_log(update: Update):
    query = update.callback_query
    activities = db.get_recent_activities(30)
    if not activities:
        text = "لا توجد عمليات مسجلة حتى الآن."
    else:
        lines = []
        for act in activities:
            _, uid, action, details, ts = act
            ts_str = ts.strftime("%m-%d %H:%M") if ts else ""
            lines.append(f"🕒 {ts_str} | 👤 {uid} | {action} | {details}")
        text = "📋 **آخر العمليات:**\n\n" + "\n".join(lines)
    keyboard = [[InlineKeyboardButton("🔙 لوحة الإدارة", callback_data="admin_panel")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

# ---------- تذاكر الدعم ----------
async def show_tickets(update: Update):
    query = update.callback_query
    tickets = db.get_open_tickets()
    if not tickets:
        text = "🎫 لا توجد تذاكر مفتوحة."
        keyboard = [[InlineKeyboardButton("🔙 لوحة الإدارة", callback_data="admin_panel")]]
    else:
        text = "🎫 **تذاكر الدعم المفتوحة:**\n\n"
        keyboard = []
        for t in tickets:
            tid, uid, subject, msg, status, reply, created = t
            text += f"🎫 #{tid} | 👤 {uid} | {subject}\n{msg[:50]}...\n\n"
            keyboard.append([InlineKeyboardButton(f"رد / إغلاق #{tid}", callback_data=f"reply_ticket_{tid}")])
        keyboard.append([InlineKeyboardButton("🔙 لوحة الإدارة", callback_data="admin_panel")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")

async def handle_reply_ticket(update: Update, context: ContextTypes.DEFAULT_TYPE, ticket_id: int):
    query = update.callback_query
    # نطلب من الأدمن إرسال الرد
    context.user_data["replying_ticket_id"] = ticket_id
    await query.message.reply_text("📝 أرسل الآن ردك على هذه التذكرة (نص واحد):")
    # سنحتاج إلى وضع مؤقت لاستقبال الرد. نضيف حالة 'waiting_ticket_reply' في context.user_data
    context.user_data["admin_action"] = "ticket_reply"

# تعديل handle_token (أو إضافة معالج) لاستقبال رد التذكرة
# سنضيف داخل handle_token قبل الأقسام الأخرى:
# if context.user_data.get("admin_action") == "ticket_reply":
#     process ticket reply

# ---------- إعدادات الأسعار والمحافظ ----------
async def show_settings(update: Update):
    query = update.callback_query
    settings = db.get_all_settings()
    text = "⚙️ **الإعدادات الحالية:**\n\n"
    keyboard = []
    for k, v in settings:
        text += f"{k}: {v}\n"
        keyboard.append([InlineKeyboardButton(f"✏️ تعديل {k}", callback_data=f"edit_setting_{k}")])
    keyboard.append([InlineKeyboardButton("🔙 لوحة الإدارة", callback_data="admin_panel")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def prompt_edit_setting(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str):
    query = update.callback_query
    context.user_data["editing_setting_key"] = key
    await query.message.reply_text(f"📝 أدخل القيمة الجديدة لـ {key}:")
    context.user_data["admin_action"] = "edit_setting"
# ---------- دوال إدارة الحسابات الفاحصة ----------
async def show_checker_management(update: Update):
    accounts = db.get_all_checkers()
    if not accounts:
        text = "❌ لا توجد حسابات فحص مضافة بعد."
        keyboard = [[InlineKeyboardButton("🔙 العودة للوحة الإدارة", callback_data="admin_panel")]]
    else:
        text = "👥 **قائمة حسابات الفحص:**\n\nاضغط على أي حساب لتبديل حالته بين مفعل ومعطل."
        keyboard = []
        for acc_id, phone, is_active in accounts:
            status_emoji = "🟢" if is_active else "🔴"
            btn_text = f"{status_emoji} - {phone}"
            keyboard.append([
                InlineKeyboardButton(btn_text, callback_data=f"toggle_chk_{acc_id}"),
                InlineKeyboardButton("🗑️ حذف", callback_data=f"delete_chk_{acc_id}")
            ])
        keyboard.append([InlineKeyboardButton("🔙 العودة للوحة الإدارة", callback_data="admin_panel")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.message.edit_text(text, reply_markup=reply_markup, parse_mode="Markdown")

async def toggle_checker_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if ADMIN_ID == 0 or user_id != ADMIN_ID:
        await query.answer("غير مصرح", show_alert=True)
        return
    try:
        acc_id = int(query.data.replace("toggle_chk_", ""))
    except ValueError:
        await query.answer("خطأ في البيانات", show_alert=True)
        return
    accounts = db.get_all_checkers()
    acc = next((a for a in accounts if a[0] == acc_id), None)
    if not acc:
        await query.message.reply_text("❌ الحساب غير موجود.")
        return
    phone = acc[1]
    old_status = acc[2]
    db.toggle_checker(acc_id)
    new_status = not old_status
    status_text = "تفعيل" if new_status else "تعطيل"
    await query.message.reply_text(f"✅ تم {status_text} الحساب `{phone}` بنجاح.")
    await show_checker_management(update)

# ---------- بقية الدوال ----------
async def force_add_checker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await start_add_checker(update, context)

async def start_add_checker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("[TRACE] Conversation started via start_add_checker")
    query = update.callback_query
    if query:
        await query.answer()
        user_id = query.from_user.id
    else:
        user_id = update.effective_user.id
    if ADMIN_ID == 0 or user_id != ADMIN_ID:
        logger.info("[TRACE] Unauthorized access attempt")
        return ConversationHandler.END
    msg_text = (
        "🚀 **نظام ربط حساب الفحص التلقائي**\n\n"
        "أرسل بيانات الحساب الفاحص بالصيغة التالية تماماً:\n"
        "`الرقم,api_id,api_hash`\n\n"
        "مثال:\n"
        "`+967777777777,28412234,b3a6c98ea...`"
    )
    if query:
        await query.message.reply_text(msg_text, parse_mode="Markdown")
    else:
        await update.message.reply_text(msg_text, parse_mode="Markdown")
    logger.info("[TRACE] State returned: PHONE")
    return PHONE

async def get_phone_and_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    logger.info(f"[TRACE] Checker state received message (PHONE state): {text}")
    try:
        phone, api_id, api_hash = text.split(",")
        context.user_data["chk_phone"] = phone.strip()
        context.user_data["chk_api_id"] = api_id.strip()
        context.user_data["chk_api_hash"] = api_hash.strip()
        await update.message.reply_text("⏳ جاري الاتصال بخوادم التليجرام وإرسال كود التحقق...")
        await login_manager.send_code(
            context.user_data["chk_phone"],
            context.user_data["chk_api_id"],
            context.user_data["chk_api_hash"]
        )
        await update.message.reply_text("💬 وصلك كود الآن على حساب التليجرام الفاحص، يرجى إرساله هنا فوراً:")
        logger.info("[TRACE] State returned: CODE")
        return CODE
    except Exception as e:
        await update.message.reply_text(f"❌ فشل الاتصال بالحساب أو أن الصيغة غير صحيحة.\nالخطأ: `{str(e)}`")
        return PHONE

async def get_code_and_verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text.strip()
    logger.info(f"[TRACE] Checker state received message (CODE state): {code}")
    phone = context.user_data["chk_phone"]
    await update.message.reply_text("⏳ جاري التحقق من كود تسجيل الدخول...")
    try:
        result = await login_manager.verify_code(phone, code)
        if result.get("status") == "CODE_EXPIRED":
            await update.message.reply_text("⏳ انتهت صلاحية الكود. تم إرسال كود جديد إلى رقمك. أرسل الكود الجديد:")
            return CODE  # يجب أن يعود إلى نفس الحالة لاستقبال الكود الجديد
        if result.get("status") == "PASSWORD_REQUIRED":
            await update.message.reply_text("🔒 هذا الحساب محمي بالتحقق بخطوتين، من فضلك أرسل باسوورد الحساب الآن:")
            return PASSWORD
        if result.get("status") == "SUCCESS":
            await update.message.reply_text(f"✅ تم ربط الحساب الفاحص بنجاح!\n👤 الاسم: {result.get('name')}")
            await login_manager.cleanup()
            return ConversationHandler.END
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ برميجي أثناء تفعيل الكود: `{str(e)}`")
        await login_manager.cleanup()
        return ConversationHandler.END
        
async def get_password_and_verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    password = update.message.text.strip()
    logger.info(f"[TRACE] Checker state received message (PASSWORD state)")
    phone = context.user_data["chk_phone"]
    await update.message.reply_text("⏳ جاري فك التحقق بخطوتين وتخزين الجلسة...")
    try:
        result = await login_manager.verify_password(phone, password)
        if result.get("status") == "SUCCESS":
            await update.message.reply_text(f"✅ تم تخطّي كلمة المرور بنجاح وحفظ الحساب الفاحص!\n👤 الاسم: {result.get('name')}")
    except Exception as e:
        await update.message.reply_text(f"❌ كلمة المرور خاطئة أو انتهت مهلة الجلسة: `{str(e)}`")
    finally:
        await login_manager.cleanup()
    logger.info("[TRACE] Conversation ended (PASSWORD SUCCESS/FAIL)")
    return ConversationHandler.END

async def cancel_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await login_manager.cleanup()
    await update.message.reply_text("❌ تم إلغاء عملية ربط الحساب الفاحص بنجاح.")
    return ConversationHandler.END

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    user_name = query.from_user.first_name

    # ---------- قسم الأدمن ----------
    if ADMIN_ID != 0 and user_id == ADMIN_ID:
        if query.data == "admin_panel":
            await show_admin_panel(update)
            return
        elif query.data == "adm_manage_checkers":
            await show_checker_management(update)
            return
        elif query.data.startswith("delete_chk_"):
            acc_id = int(query.data.replace("delete_chk_", ""))
            db.delete_checker(acc_id)
            await query.answer("🗑️ تم حذف الحساب الفاحص", show_alert=False)
            await show_checker_management(update)
            return
        elif query.data.startswith("toggle_chk_"):
            await toggle_checker_callback(update, context)
            return
        elif query.data == "adm_add_days":
            context.user_data["admin_action"] = "add_days"
            await query.message.reply_text("📥 أرسل معرف المستخدم وعدد الأيام مفصولين بمسافة:\nمثال: `834033986 30`")
            return
        elif query.data == "adm_ban":
            context.user_data["admin_action"] = "ban"
            await query.message.reply_text("📥 أرسل معرف المستخدم وحالته مفصولين بمسافة:\n(`1` للحظر أو `0` لإلغاء الحظر)\nمثال: `834033986 1`")
            return
        elif query.data == "adm_delete_user":
            context.user_data["admin_action"] = "delete_user"
            await query.message.reply_text("🗑️ أرسل `ID المستخدم` المراد مسحه تماماً من السيرفر وإلغاء بوتاته:")
            return
        elif query.data == "adm_get_ids":
            try:
                table_name = get_correct_table_name()
                conn = db.get_connection()
                cursor = conn.cursor()
                cursor.execute(f"SELECT user_id FROM {table_name}")
                rows = cursor.fetchall()
                cursor.close()
                conn.close()
                if rows:
                    user_list = "\n".join([f"👤 ID: `{row[0]}`" for row in rows])
                else:
                    user_list = "لا يوجد مستخدمين مسجلين حالياً."
                text = f"🆔 **قائمة معرّفات مستخدمي النظام التفصيلية:**\n*(تم القراءة من جدول: `{table_name}`)*\n\n{user_list}"
            except Exception as e:
                text = f"❌ تعذر استخراج المعرفات تلقائياً: {e}"
            keyboard = [[InlineKeyboardButton("🔙 العودة للوحة الإدارة", callback_data="admin_panel")]]
            await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
            return

        elif query.data == "adm_user_management":
            await show_user_management(update)
            return
        elif query.data.startswith("user_page_"):
            page = int(query.data.split("_")[-1])
            await show_user_management(update, page)
            return
        elif query.data.startswith("user_detail_"):
            uid = int(query.data.split("_")[-1])
            await show_user_detail(update, uid)
            return
        elif query.data == "adm_activity_log":
            await show_activity_log(update)
            return
        elif query.data == "adm_tickets":
            await show_tickets(update)
            return
        elif query.data.startswith("reply_ticket_"):
            tid = int(query.data.split("_")[-1])
            await handle_reply_ticket(update, context, tid)
            return
        elif query.data == "adm_settings":
            await show_settings(update)
            return
        elif query.data.startswith("edit_setting_"):
            key = query.data[len("edit_setting_"):]
            await prompt_edit_setting(update, context, key)
            return
        # زر تأكيد الدفع (للأدمن فقط)
        elif query.data.startswith("confirm_pay_"):
            if user_id != ADMIN_ID:
                await query.answer("غير مصرح", show_alert=True)
                return
            target_id = int(query.data.split("_")[2])
            pending = db.get_pending_subscription(target_id)
            if not pending:
                await query.answer("لا يوجد طلب معلق.", show_alert=True)
                return
            plan, method, amount, wallet, _ = pending

            if "حسابين" in plan:
                plan_num = "2"
            elif "3 حسابات" in plan:
                plan_num = "3"
            else:
                plan_num = "1"

            db.add_days_to_user(target_id, 30, plan_type=plan_num)
            db.log_activity(target_id, "تفعيل اشتراك", f"خطة {plan} - 30 يوم")
            db.log_activity(ADMIN_ID, "تأكيد دفع", f"مستخدم {target_id} - خطة {plan}")
            db.delete_pending_subscription(target_id)
            new_data = db.get_bot(target_id)
            if new_data:
                expires_at = new_data[2]
                if isinstance(expires_at, str):
                    expires_at = datetime.fromisoformat(expires_at.replace("Z", ""))
                expiry_str = expires_at.strftime("%Y-%m-%d %H:%M:%S")
                success_msg = (
                    f"🗒 ❛ ≽ تم دفع الفاتورة بنجاح.. ≼\n\n"
                    f"🔋 ❛ نوعية الإشتراك ≽ DurianRCS ({plan}) ≼\n"
                    f"⏰ ❛ الأيام المضافة ≽ 30 يوم ≼\n"
                    f"⏰ ❛ اشتراكك الجديد ينتهي في ≽ {expiry_str} ≼"
                )
                try:
                    await context.bot.send_message(chat_id=target_id, text=success_msg)
                except Exception as e:
                    logger.error(f"فشل إرسال رسالة نجاح الدفع للمستخدم {target_id}: {e}")
                    await query.answer("تم تأكيد الدفع ولكن تعذر إرسال إشعار للمستخدم.", show_alert=True)
                    return
                await query.answer("تم تأكيد الدفع وإرسال الإشعار.", show_alert=True)
            else:
                await query.answer("المستخدم غير موجود.", show_alert=True)
            await query.message.edit_text(query.message.text + "\n\n✅ تم التأكيد.")
            return

    # ---------- أزرار الاشتراك للمستخدمين الجدد ----------
    elif query.data == "subscribe":
        keyboard = [
            [InlineKeyboardButton("DurianRCS (حساب واحد) - 4$", callback_data="plan_1")],
            [InlineKeyboardButton("DurianRCS (حسابين) - 6$", callback_data="plan_2")],
            [InlineKeyboardButton("DurianRCS (3 حسابات) - 8$", callback_data="plan_3")],
        ]
        await query.message.edit_text("اختر خطة الاشتراك:", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    elif query.data.startswith("plan_"):
        plan_num = query.data.split("_")[1]
        context.user_data["selected_plan"] = plan_num
        prices = {"1": "4", "2": "6", "3": "8"}
        price = prices[plan_num]
        text = f"📋 اختر طريقة الدفع لـ DurianRCS ({'حساب واحد' if plan_num=='1' else 'حسابين' if plan_num=='2' else '3 حسابات'}):\n🔹 قيمة الاشتراك : {price}$"
        keyboard = [
            [InlineKeyboardButton(f"الدفع ب USDT (Binance)", callback_data=f"pay_usdt_{plan_num}")],
            [InlineKeyboardButton(f"الدفع ب TRX (Tron)", callback_data=f"pay_trx_{plan_num}")],
        ]
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    elif query.data.startswith("pay_"):
        _, method, plan_num = query.data.split("_")
        method = method.upper()

        # --- جلب الأسعار والمحافظ من الإعدادات الديناميكية ---
        plan_price = float(db.get_setting(f'plan_price_{plan_num}', '0'))
        usdt_rate = float(db.get_setting('usdt_rate', '1'))
        trx_rate = float(db.get_setting('trx_rate', '0.16'))
        usdt_wallet = db.get_setting('usdt_wallet', 'TYourUSDTAddressHere')
        trx_wallet = db.get_setting('trx_wallet', 'TSDqje1oWAcDY8Q5XzUDLWksWMSPqxv3PB')

        # حساب المبلغ بالعملة الرقمية بناءً على سعر الصرف
        if method == "USDT":
            amount = round(plan_price / usdt_rate, 2) if usdt_rate else plan_price
            wallet = usdt_wallet
            currency = "USDT"
        else:  # TRX
            amount = round(plan_price / trx_rate, 2) if trx_rate else plan_price * 6.25
            wallet = trx_wallet
            currency = "TRX"

        plan_name = "حساب واحد" if plan_num == "1" else "حسابين" if plan_num == "2" else "3 حسابات"

        db.add_pending_subscription(user_id, plan_name, currency, amount, wallet)

        # إرسال إشعار للإدارة
        admin_msg = (
            f"🔔 طلب اشتراك جديد:\n"
            f"👤 المستخدم: `{user_id}`\n"
            f"📦 الخطة: {plan_name}\n"
            f"💲 المبلغ: {amount} {currency}\n"
            f"📅 الوقت: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )
        admin_keyboard = [[InlineKeyboardButton("✅ تأكيد الدفع", callback_data=f"confirm_pay_{user_id}")]]
        await context.bot.send_message(chat_id=ADMIN_ID, text=admin_msg, reply_markup=InlineKeyboardMarkup(admin_keyboard))

        # رد للمستخدم
        text = (
            f"💰 لإيداع {amount} {currency}، يرجى إرسال المبلغ إلى العنوان التالي خلال 10 دقائق:\n\n"
            f"<code>{wallet}</code>\n\n"
            f"✅ سيتم تفعيل الاشتراك فورًا بعد وصول {amount} {currency}"
        )
        try:
            await query.message.edit_text(text, parse_mode="HTML")
        except Exception as e:
            logger.warning(f"فشل تحرير رسالة الدفع: {e}")
            await query.message.reply_text(text, parse_mode="HTML")
        return
    # ---------- باقي الأزرار العامة ----------
    if query.data == "main_menu":
        await show_dashboard(update, user_id, user_name)
        return

    db_data = db.get_bot(user_id)
    token = db_data[0] if (db_data and len(db_data) > 0) else None

    if query.data == "show_token_info":
        if not token:
            text = (
                f"📝 يرجى إرسال توكن البوت فقط:\n\n"
                f"مثال:\n"
                f"<code>123456789:ABC-DEF1234ghIkl-zyx57W2v1u123ew</code>"
            )
            await query.message.reply_text(text, parse_mode="HTML")
            # نجعل البوت ينتظر التوكن الجديد
            context.user_data["waiting_for_token"] = True
        else:
            await query.message.reply_text(f"🔑 توكنك المسجل الحالي هو:\n<code>{token}</code>", parse_mode="HTML")
        return
    elif query.data == "run_bot":
        if not token:
            await query.message.reply_text("⚠️ يرجى إرسال توكن البوت أولاً لربطه.")
        else:
            # جلب عدد الحسابات النشطة لمعرفة نوع البوت
            active_accounts = db.get_active_site_accounts(user_id)
            num_accounts = len(active_accounts) if active_accounts else 0
            if num_accounts == 1:
                bot_type = "حساب واحد"
            elif num_accounts == 2:
                bot_type = "حسابين"
            elif num_accounts >= 3:
                bot_type = f"{num_accounts} حسابات"
            else:
                bot_type = "غير محدد"
            
            success = await bot_manager.start_bot(user_id, token)
            if success:
                text = (
                    f"✅ تم تشغيل البوت بنجاح!\n"
                    f"🔹 نوع البوت: DurianRCS ({bot_type})\n\n"
                    f"⚠️ اذا كان بوتك DURIAN ولم يتم تشغيل البوت انتظر 5 دقائق لا تقم بإيقافه"
                )
                await query.message.reply_text(text)
            else:
                await query.message.reply_text("ℹ️ البوت الفرعي يعمل بالفعل في الخلفية.")
            await show_dashboard(update, user_id, user_name)
    elif query.data == "stop_bot":
        if not token:
            await query.message.reply_text("❌ ليس لديك بوت نشط لإيقافه.")
        else:
            await query.message.reply_text("⏳ جاري إيقاف البوت، يرجى الانتظار...")
            await bot_manager.stop_bot(user_id)
            await query.message.reply_text("🛑 تم إيقاف البوت بنجاح.")
            await show_dashboard(update, user_id, user_name)
    elif query.data == "renew_subscription":
        await query.message.reply_text(
            f"⚙️ **لتجديد اشتراكك الشهري:**\n\n"
            f"يرجى التواصل مع الإدارة مباشرة وتزويدهم بالمعرف الخاص بك لتفعيل باقتك:\n"
            f"🆔 معرف حسابك: `{user_id}`", parse_mode="Markdown"
        )
    elif query.data in ["contact_support", "unban_bot"]:
        await query.message.reply_text("ℹ️ هذا الخيار قيد التهيئة الفنية حالياً.")

async def safe_restore():
    try:
        await bot_manager.restore_active_bots()
    except Exception as e:
        logger.error(f"Error restoring bots on startup: {e}")

async def main():
    try:
        await db.init_db()
    except Exception as e:
        logger.error(f"Database init error: {e}")

    request_config = HTTPXRequest(connect_timeout=20.0, read_timeout=20.0)
    main_app = Application.builder().token(MAIN_TOKEN).request(request_config).build()

    checker_conv = ConversationHandler(
        entry_points=[
            CommandHandler("add_checker", force_add_checker),
            CallbackQueryHandler(start_add_checker, pattern="^adm_add_checker$")
        ],
        states={
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone_and_send)],
            CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_code_and_verify)],
            PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_password_and_verify)],
        },
        fallbacks=[CommandHandler("cancel", cancel_process)],
        per_message=False
    )

    logger.info("[TRACE] Registering checker_conv")
    main_app.add_handler(checker_conv)

    main_app.add_handler(CommandHandler("start", start))
    main_app.add_handler(CommandHandler("admin", admin_command))
    main_app.add_handler(CallbackQueryHandler(button_handler))

    async def debug_handle_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.info(f"[TRACE] handle_token RECEIVED message: '{update.message.text}'")
        return await handle_token(update, context)

    main_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, debug_handle_token))

    await main_app.initialize()
    await main_app.updater.start_polling()
    await main_app.start()

    asyncio.create_task(safe_restore())

    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        await main_app.updater.stop()
        await main_app.stop()
        await main_app.shutdown()

if __name__ == '__main__':
    asyncio.run(main())
