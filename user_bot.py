import asyncio
import logging
import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes, CallbackContext
from telegram.request import HTTPXRequest
from telegram.constants import ParseMode
import database as db
from durian_api import DurianAPI
from checker_manager import checker_manager



logger = logging.getLogger(__name__)

# ---------- خرائط الدول والأعلام ----------
COUNTRY_MAP = {
    "679": {"name": "فيجي", "emoji": "🇫🇯"},
    "33": {"name": "فرنسا", "emoji": "🇫🇷"},
    "36": {"name": "هنغاريا", "emoji": "🇭🇺"},
    "373": {"name": "مولدوفا", "emoji": "🇲🇩"},
    "7": {"name": "روسيا", "emoji": "🇷🇺"},
    "1": {"name": "أمريكا", "emoji": "🇺🇸"},
    "20": {"name": "مصر", "emoji": "🇪🇬"},
}

ALL_COUNTRIES = [
    {"name": "روسيا 🇷🇺", "code": "ru"}, {"name": "أمريكا 🇺🇸", "code": "us"},
    {"name": "إندونيسيا 🇮🇩", "code": "id"}, {"name": "مصر 🇪🇬", "code": "eg"},
    {"name": "بريطانيا 🇬🇧", "code": "uk"}, {"name": "الهند 🇮🇳", "code": "in"},
    {"name": "البرازيل 🇧🇷", "code": "br"}, {"name": "المغرب 🇲🇦", "code": "ma"},
    {"name": "الجزائر 🇩🇿", "code": "dz"}, {"name": "تونس 🇹🇳", "code": "tn"},
    {"name": "العراق 🇮🇶", "code": "iq"}, {"name": "الأردن 🇯🇴", "code": "jo"},
    {"name": "السعودية 🇸🇦", "code": "sa"}, {"name": "الإمارات 🇦🇪", "code": "ae"},
    {"name": "الكويت 🇰🇼", "code": "kw"}, {"name": "البحرين 🇧🇭", "code": "bh"},
    {"name": "عمان 🇴🇲", "code": "om"}, {"name": "قطر 🇶🇦", "code": "qa"},
    {"name": "اليمن 🇾🇪", "code": "ye"}, {"name": "فلسطين 🇵🇸", "code": "ps"},
    {"name": "لبنان 🇱🇧", "code": "lb"}, {"name": "سوريا 🇸🇾", "code": "sy"},
    {"name": "السودان 🇸🇩", "code": "sd"}, {"name": "ليبيا 🇱🇾", "code": "ly"},
    {"name": "تركيا 🇹🇷", "code": "tr"}, {"name": "ألمانيا 🇩🇪", "code": "de"},
    {"name": "فرنسا 🇫🇷", "code": "fr"}, {"name": "إسبانيا 🇪🇸", "code": "es"},
    {"name": "إيطاليا 🇮🇹", "code": "it"}, {"name": "كندا 🇨🇦", "code": "ca"},
    {"name": "أستراليا 🇦🇺", "code": "au"}, {"name": "الصين 🇨🇳", "code": "cn"},
    {"name": "اليابان 🇯🇵", "code": "jp"}, {"name": "كوريا 🇰🇷", "code": "kr"},
    {"name": "فيتنام 🇻🇳", "code": "vn"}, {"name": "تايلاند 🇹🇭", "code": "th"},
    {"name": "ماليزيا 🇲🇾", "code": "my"}, {"name": "الفلبين 🇵🇭", "code": "ph"},
    {"name": "باكستان 🇵🇰", "code": "pk"}, {"name": "أفغانستان 🇦🇫", "code": "af"},
    {"name": "إيران 🇮🇷", "code": "ir"}, {"name": "كولومبيا 🇨🇴", "code": "co"},
    {"name": "المكسيك 🇲🇽", "code": "mx"}, {"name": "الأرجنتين 🇦🇷", "code": "ar"},
    {"name": "بيرو 🇵🇪", "code": "pe"}, {"name": "فنزويلا 🇻🇪", "code": "ve"},
    {"name": "تشيلي 🇨🇱", "code": "cl"}, {"name": "أوكرانيا 🇺🇦", "code": "ua"},
    {"name": "بولندا 🇵🇱", "code": "pl"}, {"name": "رومانيا 🇷🇴", "code": "ro"},
    {"name": "هولندا 🇳🇱", "code": "nl"}, {"name": "بلجيكا 🇧🇪", "code": "be"},
    {"name": "السويد 🇸🇪", "code": "se"}, {"name": "النرويج 🇳🇴", "code": "no"},
    {"name": "البرتغال 🇵🇹", "code": "pt"}, {"name": "جنوب أفريقيا 🇿🇦", "code": "za"},
    {"name": "نيجيريا 🇳🇬", "code": "ng"}, {"name": "كينيا 🇰🇪", "code": "ke"},
    {"name": "غانا 🇬🇭", "code": "gh"}, {"name": "إثيوبيا 🇪🇹", "code": "et"},
    {"name": "موريتانيا 🇲🇷", "code": "mr"}, {"name": "أوزبكستان 🇺🇿", "code": "uz"},
    {"name": "كازاخستان 🇰🇿", "code": "kz"}, {"name": "قرغيزستان 🇰🇬", "code": "kg"},
    {"name": "طاجيكستان 🇹🇯", "code": "tj"}, {"name": "تركمانستان 🇹🇲", "code": "tm"},
    {"name": "أذربيجان 🇦🇿", "code": "az"}, {"name": "جورجيا 🇬🇪", "code": "ge"},
    {"name": "أرمينيا 🇦🇲", "code": "am"}, {"name": "النمسا 🇦🇹", "code": "at"},
    {"name": "سويسرا 🇨🇭", "code": "ch"}, {"name": "اليونان 🇬🇷", "code": "gr"},
    {"name": "بلغاريا 🇧🇬", "code": "bg"}, {"name": "كرواتيا 🇭🇷", "code": "hr"},
    {"name": "صربيا 🇷🇸", "code": "rs"}, {"name": "جمهورية التشيك 🇨🇿", "code": "cz"},
    {"name": "المجر 🇭🇺", "code": "hu"}, {"name": "الدانمارك 🇩🇰", "code": "dk"},
    {"name": "فنلندا 🇫🇮", "code": "fi"}, {"name": "أيرلندا 🇮🇪", "code": "ie"},
    {"name": "نيوزيلندا 🇳🇿", "code": "nz"}, {"name": "سنغافورة 🇸🇬", "code": "sg"},
    {"name": "بغلاديش 🇧🇩", "code": "bd"}, {"name": "سريلانكا 🇱🇰", "code": "lk"},
    {"name": "نيبال 🇳🇵", "code": "np"}, {"name": "ميانمار 🇲🇲", "code": "mm"},
    {"name": "كمبوديا 🇰🇭", "code": "kh"}, {"name": "لاوس 🇱🇦", "code": "la"},
    {"name": "منغوليا 🇲🇳", "code": "mn"}, {"name": "أنغولا 🇦🇴", "code": "ao"},
    {"name": "الكاميرون 🇨🇲", "code": "cm"}, {"name": "ساحل العاج 🇨🇮", "code": "ci"},
    {"name": "السنغال 🇸🇳", "code": "sn"}, {"name": "زيمبابوي 🇿🇼", "code": "zw"},
    {"name": "تنزانيا 🇹🇿", "code": "tz"}, {"name": "أوغندا 🇺🇬", "code": "ug"},
    {"name": "زامبيا 🇿🇲", "code": "zm"}, {"name": "مدغشقر 🇲🇬", "code": "mg"},
    {"name": "كوبا 🇨🇺", "code": "cu"}, {"name": "بنما 🇵🇦", "code": "pa"},
    {"name": "كوستاريكا 🇨🇷", "code": "cr"}, {"name": "جامايكا 🇯🇲", "code": "jm"},
    {"name": "الأوروغواي 🇺🇾", "code": "uy"}, {"name": "الباراغواي 🇵🇾", "code": "py"},
    {"name": "بوليفيا 🇧🇴", "code": "bo"}, {"name": "الإكوادور 🇪🇨", "code": "ec"},
    {"name": "أيسلندا 🇮🇸", "code": "is"}, {"name": "قبرص 🇨🇾", "code": "cy"},
    {"name": "مالطا 🇲🇹", "code": "mt"}, {"name": "ألبانيا 🇦🇱", "code": "al"},
    {"name": "أندورا 🇦🇩", "code": "ad"}, {"name": "موناكو 🇲🇨", "code": "mc"},
    {"name": "سان مارينو 🇸🇲", "code": "sm"}, {"name": "جزر البهاما 🇧🇸", "code": "bs"},
    {"name": "باربادوس 🇧🇧", "code": "bb"}, {"name": "بليز 🇧🇿", "code": "bz"},
    {"name": "غويانا 🇬🇾", "code": "gy"}, {"name": "سورينام 🇸🇷", "code": "sr"},
    {"name": "فيجي 🇫🇯", "code": "fj"}, {"name": "بابوا غينيا 🇵🇬", "code": "pg"},
    {"name": "جزر المالديف 🇲🇻", "code": "mv"}, {"name": "بروناي 🇧🇳", "code": "bn"},
    {"name": "بوتان 🇧🇹", "code": "bt"}
]

# قاموس مساعد لاستخراج اسم الدولة والإيموجي من الكود
COUNTRY_INFO = {}
for c in ALL_COUNTRIES:
    code = c["code"].upper()
    parts = c["name"].split(" ")
    emoji = parts[-1] if len(parts) > 1 else "🌐"
    name = " ".join(parts[:-1]) if len(parts) > 1 else c["name"]
    COUNTRY_INFO[code] = {"name": name, "emoji": emoji}
COUNTRY_INFO.update({
    "RU": {"name": "روسيا", "emoji": "🇷🇺"},
    "US": {"name": "أمريكا", "emoji": "🇺🇸"},
    "EG": {"name": "مصر", "emoji": "🇪🇬"},
    "SY": {"name": "سوريا", "emoji": "🇸🇾"},
    "IQ": {"name": "العراق", "emoji": "🇮🇶"},
    "SA": {"name": "السعودية", "emoji": "🇸🇦"},
    "AE": {"name": "الإمارات", "emoji": "🇦🇪"},
    "KW": {"name": "الكويت", "emoji": "🇰🇼"},
    "BH": {"name": "البحرين", "emoji": "🇧🇭"},
    "OM": {"name": "عمان", "emoji": "🇴🇲"},
    "QA": {"name": "قطر", "emoji": "🇶🇦"},
    "YE": {"name": "اليمن", "emoji": "🇾🇪"},
    "PS": {"name": "فلسطين", "emoji": "🇵🇸"},
    "LB": {"name": "لبنان", "emoji": "🇱🇧"},
    "JO": {"name": "الأردن", "emoji": "🇯🇴"},
    "TR": {"name": "تركيا", "emoji": "🇹🇷"},
    "DE": {"name": "ألمانيا", "emoji": "🇩🇪"},
    "FR": {"name": "فرنسا", "emoji": "🇫🇷"},
    "GB": {"name": "بريطانيا", "emoji": "🇬🇧"},
    "IN": {"name": "الهند", "emoji": "🇮🇳"},
    "BR": {"name": "البرازيل", "emoji": "🇧🇷"},
    "MA": {"name": "المغرب", "emoji": "🇲🇦"},
    "DZ": {"name": "الجزائر", "emoji": "🇩🇿"},
    "TN": {"name": "تونس", "emoji": "🇹🇳"},
    "LY": {"name": "ليبيا", "emoji": "🇱🇾"},
    "SD": {"name": "السودان", "emoji": "🇸🇩"},
})

# عداد مؤقت لتكرار نزول الرقم (بحد أقصى لكل مستخدم لمنع تسرب الذاكرة على المدى الطويل - 24/7)
repeat_tracker = {}
REPEAT_TRACKER_MAX_PER_USER = 2000  # أقصى عدد أرقام يُحتفظ بها لكل مستخدم قبل تنظيف الأقدم

MAX_CONCURRENT_REQUESTS = 2
# Semaphore لكل مستخدم بدل Semaphore عالمي واحد يتشاركه كل المستخدمين،
# حتى لا يُصبح مستخدم واحد كثيف الطلبات عنق زجاجة لبقية المستخدمين على نفس السيرفر.
_user_semaphores = {}

def _get_user_semaphore(user_id):
    sem = _user_semaphores.get(user_id)
    if sem is None:
        sem = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
        _user_semaphores[user_id] = sem
    return sem

# ==================== كاش DB: تجديد كل 30 ثانية فقط ====================
_db_cache = {}          # {user_id: {"accounts": [...], "channel": ..., "countries": [...]}}
_db_cache_ts = {}       # {user_id: timestamp آخر تحديث}
_db_cache_locks = {}    # {user_id: asyncio.Lock} يمنع Cache Stampede عند تزامن أول قراءة بعد انتهاء الكاش
DB_CACHE_TTL = 30       # ثانية

def _get_cache_lock(user_id):
    lock = _db_cache_locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _db_cache_locks[user_id] = lock
    return lock
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
    channel = await asyncio.to_thread(db.get_hunting_channel, user_id)
    channel_status = f"✅ مربوطة ({channel})" if channel else "❌ غير مضافة"
    text = (
        f"⚙️ **قائمة الإعدادات:**\n\n"
        f"قناة الصيد الحالية: {channel_status}\n\n"
        f"قم بتعيين الإعدادات الأساسية للبوت قبل البدء في الصيد"
    )
    keyboard = [
        [InlineKeyboardButton("‹ إضافة قناة الصيد ✅ ›", callback_data="add_hunting_channel")],
        [InlineKeyboardButton("‹ إدارة الحسابات ›", callback_data="manage_accounts")],
        [InlineKeyboardButton("‹ الباديات المرغوبة ›", callback_data="desired_prefixes")],
        [InlineKeyboardButton("‹ اللغة العربية 🌍 ›", callback_data="change_language")],
        [InlineKeyboardButton("‹ رجوع ›", callback_data="main_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.message.edit_text(text, reply_markup=reply_markup)

# ==================== 3. إدارة الحسابات ====================
async def show_manage_accounts(update: Update, user_id: int):
    try:
        accounts = await asyncio.to_thread(db.get_all_site_accounts, user_id)
        plan = await asyncio.to_thread(db.get_user_plan, user_id)  # "1", "2", "3"
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
    """عرض تفاصيل حساب واحد مع أزرار الإجراءات"""
    accounts = await asyncio.to_thread(db.get_all_site_accounts, user_id)
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
        await asyncio.to_thread(db.save_hunting_channel, user_id, text)
        if user_id in _db_cache:
            _db_cache.pop(user_id, None)
        keyboard = [[InlineKeyboardButton("⬅️ العودة للإعدادات", callback_data="bot_settings")]]
        await update.message.reply_text(
            f"✅ **تم ربط قناة الصيد بنجاح!**\n\n🆔 معرف القناة المسجل: `{text}`\n\n"
            f"⚠️ **ملاحظة هامة:** تأكد من رفع هذا البوت كـ **مشرف (Admin)** داخل القناة ومنحه صلاحية 'نشر الرسائل' لتتمكن المنصة من إنزال الأرقام فيها تلقائياً.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return
    elif context.user_data.get("waiting_for_username"):
        context.user_data["temp_username"] = text
        context.user_data.pop("waiting_for_username", None)
        context.user_data["waiting_for_apikey"] = True
        await update.message.reply_text("🔑 ممتاز، الآن قم بإرسال الـ **API Key** الخاص بك من إعدادات حسابك في الموقع:")
        return
    elif context.user_data.get("waiting_for_apikey"):
        username = context.user_data.get("temp_username")
        api_key = text
        context.user_data.pop("waiting_for_apikey", None)
        context.user_data.pop("temp_username", None)
        await asyncio.to_thread(db.save_site_account_v2, user_id, username, api_key)
        if user_id in _db_cache:
            _db_cache.pop(user_id, None)
        keyboard = [[InlineKeyboardButton("⬅️ العودة لإدارة الحسابات", callback_data="manage_accounts")]]
        await update.message.reply_text(
            f"✅ تم إضافة الحساب بنجاح وتفعيله!\n\n👤 اسم المستخدم: `{username}`\n🔑 الـ API Key تم حفظه.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
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
            await asyncio.to_thread(db.toggle_site_account, user_id, acc_id)
            if user_id in _db_cache:
                _db_cache.pop(user_id, None)
        except Exception as e:
            if "MAX_ACTIVE_REACHED" in str(e):
                plan = await asyncio.to_thread(db.get_user_plan, user_id)
                await query.answer(f"❌ خطتك تسمح بتفعيل {plan} حسابات فقط.", show_alert=True)
            else:
                await query.answer(f"❌ خطأ: {e}", show_alert=True)
            return
        await query.answer("✅ تم تبديل حالة الحساب", show_alert=False)
        await show_account_detail(update, user_id, acc_id)
        return
        
    elif data.startswith("delete_site_"):
        acc_id = int(data.split("_")[2])
        accounts = await asyncio.to_thread(db.get_all_site_accounts, user_id)
        if len(accounts) == 1:
            await query.answer("❌ لا يمكن حذف الحساب الوحيد. أضف حساباً آخر أولاً.", show_alert=True)
            return
        await asyncio.to_thread(db.delete_site_account, user_id, acc_id)
        if user_id in _db_cache:
            _db_cache.pop(user_id, None)
        await query.answer("🗑️ تم حذف الحساب", show_alert=False)
        # العودة إلى قائمة الحسابات بعد الحذف
        await show_manage_accounts(update, user_id)
        return
    elif data == "noop":
        await query.answer()
        return
    elif data == "add_hunting_channel":
        context.user_data["waiting_for_channel_id"] = True
        await query.message.reply_text(
            "📥 **قم بإنشاء قناة عامة أو خاصة الآن، ثم اتبع الخطوات التالية:**\n\n"
            "1️⃣ قم إضافة هذا البوت كـ **مشرف (Admin)** داخل القناة.\n"
            "2️⃣ قم بنسخ **معرف القناة (Channel ID)** وإرساله هنا كرسالة نصية.\n\n"
            "💡 *نصيحة:* إذا كانت القناة عامة، أرسل الرابط المخفف كمعرف (مثل: `@MyHuntingChannel`). وإذا كانت خاصة، أرسل معرفها الرقمي الطويل المبتدئ بـ -100."
        )

    elif data == "add_new_site_account":
        plan = await asyncio.to_thread(db.get_user_plan, user_id)
        max_accounts = int(plan)
        accounts = await asyncio.to_thread(db.get_all_site_accounts, user_id)
        if len(accounts) >= max_accounts:
            await query.answer(f"❌ خطتك تسمح بـ {max_accounts} حسابات فقط.", show_alert=True)
            return
        context.user_data["waiting_for_username"] = True
        await query.message.reply_text("📥 فضلاً، أرسل الآن **اسم المستخدم (Username)** الخاص بحسابك في موقع DurianRCS:")
        
    elif data == "manage_countries":
        await show_manage_countries(update, user_id)
        return
    elif data.startswith("delete_country_"):
        country_code = data.split("_", 2)[-1]
        await asyncio.to_thread(db.delete_user_country, user_id, country_code)
        if user_id in _db_cache:
            _db_cache.pop(user_id, None)
        await query.answer("🗑️ تم حذف الدولة", show_alert=False)
        await show_manage_countries(update, user_id)
        return
    elif data == "start_hunting":
        active_accounts = await asyncio.to_thread(db.get_active_site_accounts, user_id)
        channel = await asyncio.to_thread(db.get_hunting_channel, user_id)
        countries = await asyncio.to_thread(db.get_user_countries, user_id)
        if not active_accounts:
            await query.message.reply_text("❌ لا يمكن تشغيل الصيد! ...")
            return
        if not channel:
            await query.message.reply_text("❌ لا يمكن تشغيل الصيد! ...")
            return
        if not countries:
            await query.message.reply_text("❌ لا يمكن تشغيل الصيد! ...")
            return
        username_first = active_accounts[0][0]
        api_key_first = active_accounts[0][1]
        balance = await DurianAPI.get_balance_by_name(username_first, api_key_first)
        current_jobs = context.job_queue.get_jobs_by_name(f"hunt_{user_id}")
        if current_jobs:
            await query.message.reply_text("ℹ️ الصيد يعمل بالفعل.")
            return

        bot_owner_id = user_id

        context.job_queue.run_repeating(
            check_and_hunt_numbers, interval=4, first=1, user_id=user_id,
            name=f"hunt_{user_id}"
        )
        await asyncio.to_thread(db.set_hunting_status, user_id, 1)
        accounts_str = "\n".join([f"👤 {u}" for u, _ in active_accounts])
        # تنبيه منبثق بنجاح التشغيل
        await query.answer(
            f"🚀 تم تشغيل الصيد بنجاح!\nالحسابات: {', '.join([u for u, _ in active_accounts])}\nالقناة: {channel}",
            show_alert=True
        )
    elif data == "stop_hunting":
        await asyncio.to_thread(db.set_hunting_status, user_id, 0)
        current_jobs = context.job_queue.get_jobs_by_name(f"hunt_{user_id}")
        if current_jobs:
            for job in current_jobs:
                job.schedule_removal()
        await query.answer("🛑 تم إيقاف الصيد بنجاح.", show_alert=True)

    elif data.startswith("country_settings_"):
        country_code = data.split("_", 2)[-1]
        await show_country_settings(update, user_id, country_code)
        return


    elif data.startswith("add_country_page_"):
        page = int(data.split("_")[-1])
        items_per_page = 23
        start_idx = page * items_per_page
        end_idx = start_idx + items_per_page
        page_countries = ALL_COUNTRIES[start_idx:end_idx]
        text = f"🗺️ **واجهة اختيار الدول - صفحة ({page + 1}):**\n\nاضغط على اسم الدولة لتفعيل الصيد منها مباشرة:"
        keyboard = []
        for i in range(0, len(page_countries), 2):
            row = []
            c1 = page_countries[i]
            row.append(InlineKeyboardButton(c1["name"], callback_data=f"save_c_{c1['name']}_{c1['code']}"))
            if i + 1 < len(page_countries):
                c2 = page_countries[i+1]
                row.append(InlineKeyboardButton(c2["name"], callback_data=f"save_c_{c2['name']}_{c2['code']}"))
            keyboard.append(row)
        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton("⬅️ السابقة", callback_data=f"add_country_page_{page - 1}"))
        if end_idx < len(ALL_COUNTRIES):
            nav_row.append(InlineKeyboardButton("التالية ➡️", callback_data=f"add_country_page_{page + 1}"))
        if nav_row:
            keyboard.append(nav_row)
        keyboard.append([InlineKeyboardButton("🔙 العودة للرئيسية", callback_data="main_menu")])
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    elif data.startswith("save_c_"):
        parts = data.split("_")
        # استخراج اسم الدولة والكود
        # الصيغة: save_c_اسم_الدولة_code
        country_name_with_emoji = parts[2]
        country_code = parts[3]
        # إذا كان اسم الدولة يحتوي على مسافات، فقد يكون هناك أجزاء إضافية
        if len(parts) > 4:
            country_name_with_emoji = "_".join(parts[2:-1])
            country_code = parts[-1]
        
        # استبدال الشرطات السفلية بمسافات
        country_name_with_emoji = country_name_with_emoji.replace("_", " ")
        
        await asyncio.to_thread(db.add_user_country, user_id, country_code)
        if user_id in _db_cache:
            _db_cache.pop(user_id, None)
        # رسالة منبثقة فقط، بدون تغيير الصفحة
        await query.answer(f"✔️ تمت إضافة {country_name_with_emoji} بنجاح", show_alert=True)
        # لا نغير الصفحة، يبقى المستخدم في نفس قائمة الدول
        return



# ==================== 6. عرض وإدارة الدول المختارة ====================
async def show_manage_countries(update: Update, user_id: int):
    """عرض قائمة الدول المختارة مع أيقونات الإعدادات"""
    countries = await asyncio.to_thread(db.get_user_countries, user_id)
    if not countries:
        text = "🌍 **لم تقم بإضافة أي دولة بعد.**\n\nاستخدم زر 'اضافه دوله' لتفعيل الصيد من دول معينة."
        keyboard = [[InlineKeyboardButton("🔙 العودة للقائمة الرئيسية", callback_data="main_menu")]]
    else:
        text = "🌍 **الدول المفعلة حاليًا:**\n\nاضغط على أي دولة للدخول إلى إعداداتها."
        keyboard = []
        for code in countries:
            country_name = code
            for c in ALL_COUNTRIES:
                if c["code"] == code:
                    country_name = c["name"]
                    break
            # زر الدولة ينقلك إلى الإعدادات
            keyboard.append([InlineKeyboardButton(f"⚙️ {country_name}", callback_data=f"country_settings_{code}")])
        keyboard.append([InlineKeyboardButton("🔙 العودة للقائمة الرئيسية", callback_data="main_menu")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.message.edit_text(text, reply_markup=reply_markup, parse_mode="Markdown")

async def show_country_settings(update: Update, user_id: int, country_code: str):
    """عرض شاشة إعدادات دولة محددة (شاشة واحدة فقط)"""
    # جلب اسم الدولة والعلم
    country_name = country_code
    country_flag = "🌐"
    for c in ALL_COUNTRIES:
        if c["code"] == country_code:
            country_name = c["name"].split(" ")[0] if " " in c["name"] else c["name"]
            country_flag = c["name"].split(" ")[-1] if " " in c["name"] else "🌐"
            break
    
    text = (
        f"🌍 **إعدادات الدولة: {country_name} {country_flag}**\n\n"
        f"يمكنك حذف الدولة من قائمة الصيد الخاصة بك عبر الضغط على الزر أدناه."
    )
    
    keyboard = [
        # زر حذف الدولة
        [InlineKeyboardButton("🗑️ حذف الدولة", callback_data=f"delete_country_{country_code}")],
        # زر الرجوع
        [InlineKeyboardButton("🔙 رجوع", callback_data="manage_countries")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.message.edit_text(text, reply_markup=reply_markup, parse_mode="Markdown")
# ==================== 7. دالة الصيد والضخ ====================
async def check_and_hunt_numbers(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    user_id = job.user_id

    now = time.perf_counter()
    cache_age = now - _db_cache_ts.get(user_id, 0)
    if user_id not in _db_cache or cache_age > DB_CACHE_TTL:
        # قفل لكل مستخدم يمنع Cache Stampede: لو دخل نداءان متزامنان لنفس المستخدم
        # لحظة انتهاء صلاحية الكاش، ينتظر الثاني بدل تكرار نفس استعلامات DB.
        async with _get_cache_lock(user_id):
            cache_age = now - _db_cache_ts.get(user_id, 0)
            if user_id not in _db_cache or cache_age > DB_CACHE_TTL:
                t_db_start = time.perf_counter()
                try:
                    active_accounts, channel, countries = await asyncio.gather(
                        asyncio.to_thread(db.get_active_site_accounts, user_id),
                        asyncio.to_thread(db.get_hunting_channel, user_id),
                        asyncio.to_thread(db.get_user_countries, user_id)
                    )
                except Exception as e:
                    # عطل DB مؤقت: لا نُلغي مهمة الصيد بسبب هذا، بل نتخطى هذه الدورة فقط
                    # ونحاول مجدداً في الدورة القادمة (بدل تعطّل صامت دائم لكل الأرقام).
                    logger.error(f"[User: {user_id}] فشل تحديث بيانات الصيد من DB (سيُعاد المحاولة لاحقاً): {e}")
                    return
                t_db_end = time.perf_counter()
                _db_cache[user_id] = {"accounts": active_accounts, "channel": channel, "countries": countries}
                _db_cache_ts[user_id] = now
                logger.info(
                    f"[PERF_TRACE] [User: {user_id}] DB refreshed (cache miss): {t_db_end - t_db_start:.4f}s"
                )
            else:
                active_accounts = _db_cache[user_id]["accounts"]
                channel        = _db_cache[user_id]["channel"]
                countries      = _db_cache[user_id]["countries"]
    else:
        active_accounts = _db_cache[user_id]["accounts"]
        channel        = _db_cache[user_id]["channel"]
        countries      = _db_cache[user_id]["countries"]
        logger.info(
            f"[PERF_TRACE] [User: {user_id}] DB served from cache (age={cache_age:.1f}s)"
        )

    if not active_accounts or not channel or not countries:
        # لا توجد بيانات فعلية (وليس عطل DB) — نوقف الصيد فعلياً ونُحدّث القاعدة بنفس اللحظة
        # حتى لا يبقى user_hunting_status يُظهر "يعمل" بينما الـ Job أُلغي فعلياً (تعارض حالة).
        job.schedule_removal()
        try:
            await asyncio.to_thread(db.set_hunting_status, user_id, 0)
        except Exception as e:
            logger.error(f"[User: {user_id}] فشل تحديث حالة الصيد إلى متوقف بعد إلغاء المهمة: {e}")
        return

    if user_id not in repeat_tracker:
        repeat_tracker[user_id] = {}

    async def process_account_country(username, api_key, country_code):
        """معالجة دولة واحدة لحساب واحد، تُنفذ بشكل متوازٍ"""
        clean_country = str(country_code).strip()
        user_semaphore = _get_user_semaphore(user_id)
        try:
            t_sem_start = time.perf_counter()
            async with user_semaphore:
                t_sem_end = time.perf_counter()
                t_api_start = time.perf_counter()
                result = await DurianAPI.order_number_by_name(username, api_key, clean_country, project_id="0257")
                t_api_end = time.perf_counter()
                
            sem_delay = t_sem_end - t_sem_start
            api_delay = t_api_end - t_api_start
            
            logger.info(
                f"[PERF_TRACE] [Task: {username}-{clean_country}] Queue/Semaphore wait={sem_delay:.4f}s, "
                f"Durian API order={api_delay:.4f}s"
            )

            # ══════════════════════════════════════════
            # [DEBUG-STEP-1] استلام الرقم من DurianRCS
            # ══════════════════════════════════════════
            logger.warning(
                f"[DEBUG][STEP-1][DURIAN_RESPONSE] "
                f"user={user_id} | account={username} | country={clean_country} | "
                f"result_status={result.get('status') if result else 'None'} | "
                f"result_keys={list(result.keys()) if result else 'None'} | "
                f"raw_result={result}"
            )

            if not result or result.get("status") != "success":
                logger.warning(
                    f"[DEBUG][STEP-1][SKIP] user={user_id} | country={clean_country} | "
                    f"REASON=result not success | status={result.get('status') if result else 'None'}"
                )
                return
            phone_number = result.get("number")
            if not phone_number:
                logger.warning(
                    f"[DEBUG][STEP-1][SKIP] user={user_id} | country={clean_country} | "
                    f"REASON=phone_number is empty | result={result}"
                )
                return

            logger.warning(
                f"[DEBUG][STEP-1][GOT_NUMBER] "
                f"user={user_id} | phone={phone_number} | country={clean_country} | account={username}"
            )

            t_number_start = time.perf_counter()

            # ══════════════════════════════════════════
            # [DEBUG-STEP-2] قبل استدعاء check_number()
            # ══════════════════════════════════════════
            logger.warning(
                f"[DEBUG][STEP-2][BEFORE_CHECK] "
                f"phone={phone_number} | calling checker_manager.check_number()"
            )

            # --- الفحص عبر Telethon: مسجل / محظور / غير مسجل ---
            try:
                check_result = await checker_manager.check_number(phone_number)
            except Exception as e:
                logger.error(
                    f"[DEBUG][STEP-2][CHECK_EXCEPTION] "
                    f"phone={phone_number} | exception_type={type(e).__name__} | error={e}"
                )
                check_result = "unknown"

            # ══════════════════════════════════════════
            # [DEBUG-STEP-3] بعد check_number()
            # ══════════════════════════════════════════
            import datetime
            logger.warning(
                f"[DEBUG][STEP-3][AFTER_CHECK] "
                f"phone={phone_number} | check_result='{check_result}' | "
                f"time={datetime.datetime.now().isoformat()}"
            )

            req_id = result.get("id") or result.get("order") or "N/A"
            logger.info(
                f"\n==============================\n"
                f"NEW PHONE RECEIVED\n"
                f"Phone: {phone_number}\n"
                f"Time: {datetime.datetime.now().isoformat()}\n"
                f"Request ID: {req_id}\n"
                f"==============================\n"
                f"[STEP 1]\n"
                f"Received from Durian API\n"
                f"\n"
                f"[STEP 2]\n"
                f"Number entered user_bot.py\n"
                f"\n"
                f"[STEP 3]\n"
                f"Telethon check: {check_result}"
            )

            if check_result == "registered":
                status_text = "🟢 مسجل"
            elif check_result == "banned":
                status_text = "🔴 محظور"
            elif check_result == "unregistered":
                status_text = "🆕 غير مسجل"
            else:
                status_text = "🟡 غير معروف"

            # ══════════════════════════════════════════
            # [DEBUG-STEP-4] بعد تحويل الحالة إلى status_text
            # ══════════════════════════════════════════
            logger.warning(
                f"[DEBUG][STEP-4][STATUS_MAPPED] "
                f"phone={phone_number} | check_result='{check_result}' | "
                f"status_text='{status_text}' | WILL_SEND_TO_CHANNEL=True"
            )

            # --- تحديد الدولة والعلم (باستخدام COUNTRY_INFO السريعة) ---
            country_name = clean_country.upper()
            country_flag = "🌐"
            if clean_country.upper() in COUNTRY_INFO:
                info = COUNTRY_INFO[clean_country.upper()]
                country_name = info["name"]
                country_flag = info["emoji"]
            else:
                # fallback للخريطة القديمة
                for prefix, info in COUNTRY_MAP.items():
                    if phone_number.replace("+", "").startswith(prefix):
                        country_name = info["name"]
                        country_flag = info["emoji"]
                        break

            # --- تحديث عداد التكرار (مع سقف أقصى لمنع تسرب الذاكرة على المدى الطويل) ---
            user_repeats = repeat_tracker[user_id]
            user_repeats[phone_number] = user_repeats.get(phone_number, 0) + 1
            repeat_count = user_repeats[phone_number]
            if len(user_repeats) > REPEAT_TRACKER_MAX_PER_USER:
                # نحذف أقدم نصف الإدخالات (بحسب ترتيب الإدراج) بدل تركها تنمو للأبد
                for old_key in list(user_repeats.keys())[: REPEAT_TRACKER_MAX_PER_USER // 2]:
                    user_repeats.pop(old_key, None)

            # --- صياغة الرسالة ---
            message_text = (
                        f"<b>🔰 تـم شـراء رقـم جـديـد مـن DurianRCS 🔰</b>\n\n"
                        f"<b>    - الـرقـــــم : <code>{phone_number}</code></b>\n"
                        f"<b>    - الـدولـة : {country_name} {country_flag}</b>\n"
                        f"<b>    - الـحـالـة : {status_text}</b>\n"
                        f"<b>    - تـكـرار نـزول الـرقـم : {repeat_count} مـرة</b>\n"
                        f"<b>    - الــكـــود : قـيـد الإنـتـظـار ❗️</b>"
                    )

            keyboard = [
                [
                    InlineKeyboardButton("- نسبة الوصول .", callback_data=f"rate_{username}_{phone_number}"),
                    InlineKeyboardButton("- ضعيفه 🧌 .", callback_data=f"weak_{username}_{phone_number}")
                ],
                [
                    InlineKeyboardButton("- طلب الكود .", callback_data=f"code_{username}_{phone_number}"),
                    InlineKeyboardButton("- فك حظر .", callback_data=f"unban_{username}_{phone_number}")
                ],
                [
                    InlineKeyboardButton("- الغاء الرقم .", callback_data=f"cancel_{username}_{phone_number}")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            t_send_start = time.perf_counter()

            # ══════════════════════════════════════════
            # [DEBUG-STEP-5] قبل إرسال send_message()
            # ══════════════════════════════════════════
            logger.warning(
                f"[DEBUG][STEP-5][BEFORE_SEND] "
                f"phone={phone_number} | status='{check_result}' | "
                f"channel={channel} | user={user_id}"
            )

            # فصل إرسال الرسالة عن باقي المنطق: الرقم اشتُري بالفعل واستُهلك رصيده من DurianRCS،
            # فلو فشل الإرسال فقط (مثلاً البوت أُزيل من صلاحيات القناة) يجب ألا يُبتلع الخطأ بصمت
            # ضمن نفس except العام؛ يجب تسجيله بوضوح كـ"رقم مدفوع فُقد" لتمييزه عن فشل السحب/الفحص.
            try:
                await context.bot.send_message(
                    chat_id=channel,
                    text=message_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup
                )
            except Exception as send_err:
                logger.error(
                    f"[DEBUG][STEP-5][SEND_FAILED] "
                    f"⚠️ [رقم مدفوع مفقود] phone={phone_number} | status='{check_result}' | "
                    f"user={user_id} | account={username} | channel={channel} | "
                    f"error_type={type(send_err).__name__} | error={send_err}"
                )
                return
            t_send_end = time.perf_counter()

            # ══════════════════════════════════════════
            # [DEBUG-STEP-6] بعد نجاح الإرسال
            # ══════════════════════════════════════════
            logger.warning(
                f"[DEBUG][STEP-6][SEND_SUCCESS] "
                f"phone={phone_number} | status='{check_result}' | "
                f"channel={channel} | duration={t_send_end - t_send_start:.4f}s"
            )
            
            logger.info(
                f"[PERF_TRACE] [Number: {phone_number}] context.bot.send_message duration: "
                f"{t_send_end - t_send_start:.4f}s"
            )
            logger.info(
                f"[PERF_TRACE] [Number: {phone_number}] Total number-to-channel duration: "
                f"{t_send_end - t_number_start:.4f}s"
            )
        except Exception as e:
            logger.error(f"Error for user {user_id}, account {username}, country {country_code}: {e}")

    # بناء قائمة المهام لجميع الحسابات والدول
    tasks = []
    for username, api_key in active_accounts:
        for country_code in countries:
            tasks.append(process_account_country(username, api_key, country_code))

    # تنفيذ جميع المهام بشكل متوازٍ
    await asyncio.gather(*tasks)

async def _user_bot_error_handler(update, context: ContextTypes.DEFAULT_TYPE):
    """
    معالج أخطاء عام لكل بوت فرعي للمستخدم. بدونه، أي استثناء غير متوقع
    كان يُعالَج داخلياً بصمت بواسطة PTB دون أي تنبيه فعلي (ضعف Observability).
    """
    logger.error(f"Unhandled exception in user sub-bot: {context.error}", exc_info=context.error)

def create_user_app(token: str):
    request_config = HTTPXRequest(
        connect_timeout=10.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=5.0,
        connection_pool_size=8,  # اتصالات HTTP دائمة
    )
    app = (
        Application.builder()
        .token(token)
        .request(request_config)
        .concurrent_updates(True)  # معالجة متوازية لطلبات الأزرار
        .build()
    )
    app.add_handler(CommandHandler("start", start_user_bot))
    app.add_handler(CallbackQueryHandler(user_bot_callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_user_inputs))
    app.add_error_handler(_user_bot_error_handler)
    return app
