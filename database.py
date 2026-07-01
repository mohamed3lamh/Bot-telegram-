import os
import time
import logging
import pg8000
from urllib.parse import urlparse

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")

def get_connection():
    if not DATABASE_URL:
        logger.error("DATABASE_URL missing in environment variables!")
        raise ValueError("DATABASE_URL is not set")

    parsed_url = urlparse(DATABASE_URL.strip())
    username = parsed_url.username
    password = parsed_url.password
    host = parsed_url.hostname
    port = parsed_url.port if parsed_url.port else 5432
    dbname = parsed_url.path.lstrip('/')

    for attempt in range(5):
        try:
            return pg8000.connect(
                user=username,
                password=password,
                host=host,
                port=port,
                database=dbname,
                ssl_context=True
            )
        except Exception as e:
            if attempt == 4:
                logger.error(f"❌ فشلت كافة محاولات الاتصال بقاعدة البيانات: {e}")
                raise e
            logger.warning(f"🔄 محاولة الاتصال بقاعدة البيانات فشلت ({attempt + 1}/5)، جاري إعادة المحاولة خلال ثانيتين...")
            time.sleep(2)

def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    # تعريف دالة column_exists قبل استخدامها
    def column_exists(table, column):
        cursor.execute(f"SELECT COUNT(*) FROM information_schema.columns WHERE table_name='{table}' AND column_name='{column}'")
        return cursor.fetchone()[0] > 0

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_bots (
            user_id BIGINT PRIMARY KEY,
            token TEXT UNIQUE NOT NULL,
            is_active INTEGER DEFAULT 0,
            expires_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP + INTERVAL '30 days',
            is_banned INTEGER DEFAULT 0
        )
    ''')
    conn.commit()

    # إضافة عمود plan_type إذا لم يكن موجوداً
    if not column_exists('user_bots', 'plan_type'):
        cursor.execute("ALTER TABLE user_bots ADD COLUMN plan_type TEXT DEFAULT '1'")
        conn.commit()

    if not column_exists('user_bots', 'expires_at'):
        cursor.execute("ALTER TABLE user_bots ADD COLUMN expires_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP + INTERVAL '30 days'")
        conn.commit()
        
    if not column_exists('user_bots', 'is_banned'):
        cursor.execute("ALTER TABLE user_bots ADD COLUMN is_banned INTEGER DEFAULT 0")
        conn.commit()

    # 🛠️ [إصلاح حاسم]: إضافة عمود is_hunting لجدول user_bots تلقائياً لتفادي انهيار المحرك
    if not column_exists('user_bots', 'is_hunting'):
        logger.info("⚙️ جاري تحديث قاعدة البيانات: إضافة عمود 'is_hunting' إلى جدول 'user_bots'...")
        cursor.execute("ALTER TABLE user_bots ADD COLUMN is_hunting INTEGER DEFAULT 0")
        conn.commit()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_hunting_channels (
            user_id BIGINT PRIMARY KEY,
            channel_id TEXT NOT NULL
        )
    ''')
    conn.commit()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_hunting_status (
            user_id BIGINT PRIMARY KEY,
            is_hunting INTEGER DEFAULT 0
        )
    ''')
    conn.commit()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_countries (
            user_id BIGINT,
            country_name TEXT,
            PRIMARY KEY (user_id, country_name)
        )
    ''')
    conn.commit()

    # إضافة أعمدة الإعدادات إذا لم تكن موجودة
    if not column_exists('user_countries', 'number_type'):
        cursor.execute("ALTER TABLE user_countries ADD COLUMN number_type TEXT DEFAULT 'all'")
        conn.commit()
    if not column_exists('user_countries', 'session_status'):
        cursor.execute("ALTER TABLE user_countries ADD COLUMN session_status TEXT DEFAULT 'all'")
        conn.commit()
        
    # --- ترقية جدول حسابات الموقع إلى V2 (متعدد) ---
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_site_accounts_v2 (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            username TEXT NOT NULL,
            api_key TEXT NOT NULL,
            is_active BOOLEAN DEFAULT FALSE,
            UNIQUE(user_id, username)
        )
    """)
    conn.commit()

    # نقل البيانات القديمة إذا كان الجدول القديم موجوداً ولم تتم ترقيته
    if not column_exists('user_site_accounts_v2', 'is_active') and column_exists('user_site_accounts', 'username') and not column_exists('user_site_accounts', 'id'):
        logger.info("Migrating old user_site_accounts to v2...")
        try:
            cursor.execute("""
                INSERT INTO user_site_accounts_v2 (user_id, username, api_key, is_active)
                SELECT user_id, username, api_key, TRUE
                FROM user_site_accounts
                ON CONFLICT (user_id, username) DO NOTHING
            """)
            conn.commit()
            cursor.execute("DROP TABLE user_site_accounts")
            conn.commit()
        except Exception as e:
            logger.warning(f"Migration error: {e}")
            conn.rollback()

    # إعادة تسمية v2 إلى الاسم الأصلي
    if column_exists('user_site_accounts_v2', 'is_active'):
        cursor.execute("DROP TABLE IF EXISTS user_site_accounts")
        conn.commit()
        cursor.execute("ALTER TABLE user_site_accounts_v2 RENAME TO user_site_accounts")
        conn.commit()
        logger.info("Renamed user_site_accounts_v2 to user_site_accounts")
    else:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_site_accounts (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                username TEXT NOT NULL,
                api_key TEXT NOT NULL,
                is_active BOOLEAN DEFAULT FALSE,
                UNIQUE(user_id, username)
            )
        """)
        conn.commit()

    # جدول حسابات الفحص
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS telegram_accounts (
            id SERIAL PRIMARY KEY,
            phone TEXT UNIQUE NOT NULL,
            api_id INTEGER NOT NULL,
            api_hash TEXT NOT NULL,
            string_session TEXT NOT NULL,
            is_active BOOLEAN DEFAULT TRUE,
            flood_until TIMESTAMP NULL,
            total_checks INTEGER DEFAULT 0,
            last_used TIMESTAMP NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()

    if not column_exists('telegram_accounts', 'is_active'):
        cursor.execute("ALTER TABLE telegram_accounts ADD COLUMN is_active BOOLEAN DEFAULT TRUE")
        conn.commit()
    if column_exists('telegram_accounts', 'status'):
        try:
            cursor.execute("ALTER TABLE telegram_accounts DROP COLUMN status")
            conn.commit()
        except:
            pass

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS activity_log (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            action TEXT NOT NULL,
            details TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()

    # ---------- تذاكر الدعم ----------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS support_tickets (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            subject TEXT DEFAULT 'دعم',
            message TEXT NOT NULL,
            status TEXT DEFAULT 'open',
            admin_reply TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()

    # ---------- إعدادات الأسعار والمحافظ ----------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    conn.commit()

    # إدخال القيم الافتراضية للإعدادات إذا لم تكن موجودة
    defaults = {
        'plan_price_1': '4',
        'plan_price_2': '6',
        'plan_price_3': '8',
        'usdt_wallet': 'TYourUSDTAddressHere',
        'trx_wallet': 'TSDqje1oWAcDY8Q5XzUDLWksWMSPqxv3PB',
        'usdt_rate': '1',   
        'trx_rate': '0.16'   
    }
    for k, v in defaults.items():
        cursor.execute("""
            INSERT INTO settings (key, value) VALUES (%s, %s)
            ON CONFLICT (key) DO NOTHING
        """, (k, v))
    conn.commit()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pending_subscriptions (
            user_id BIGINT PRIMARY KEY,
            plan TEXT NOT NULL,
            payment_method TEXT NOT NULL,
            amount_crypto TEXT NOT NULL,
            wallet_address TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()

    cursor.close()
    conn.close()

# --- دوال حسابات DurianRCS (متعددة) ---
def save_site_account_v2(user_id, username, api_key):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        plan = get_user_plan(user_id)
        max_accounts = int(plan)
        cursor.execute("SELECT COUNT(*) FROM user_site_accounts WHERE user_id = %s", (user_id,))
        count = cursor.fetchone()[0]
        if count >= max_accounts:
            raise Exception("MAX_ACCOUNTS_REACHED")
        cursor.execute("""
            INSERT INTO user_site_accounts (user_id, username, api_key, is_active)
            VALUES (%s, %s, %s, TRUE)
            ON CONFLICT (user_id, username) DO UPDATE SET api_key = EXCLUDED.api_key, is_active = TRUE
        """, (user_id, username, api_key))
        conn.commit()
    finally:
        cursor.close()
        conn.close()

def get_all_site_accounts(user_id):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id, username, api_key, is_active FROM user_site_accounts WHERE user_id = %s ORDER BY id", (user_id,))
        rows = cursor.fetchall()
        return rows
    finally:
        cursor.close()
        conn.close()

def toggle_site_account(user_id, account_id):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT is_active FROM user_site_accounts WHERE id = %s AND user_id = %s", (account_id, user_id))
        row = cursor.fetchone()
        if not row:
            return
        current_status = row[0]
        if not current_status:  
            plan = get_user_plan(user_id)
            max_active = int(plan)
            cursor.execute("SELECT COUNT(*) FROM user_site_accounts WHERE user_id = %s AND is_active = TRUE", (user_id,))
            active_count = cursor.fetchone()[0]
            if active_count >= max_active:
                raise Exception("MAX_ACTIVE_REACHED")
        cursor.execute("""
            UPDATE user_site_accounts
            SET is_active = NOT is_active
            WHERE id = %s AND user_id = %s
        """, (account_id, user_id))
        conn.commit()
    finally:
        cursor.close()
        conn.close()

def delete_site_account(user_id, account_id):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM user_site_accounts WHERE id = %s AND user_id = %s", (account_id, user_id))
        cursor.execute("SELECT COUNT(*) FROM user_site_accounts WHERE user_id = %s AND is_active = TRUE", (user_id,))
        if cursor.fetchone()[0] == 0:
            cursor.execute("UPDATE user_site_accounts SET is_active = TRUE WHERE id = (SELECT id FROM user_site_accounts WHERE user_id = %s LIMIT 1)", (user_id,))
        conn.commit()
    finally:
        cursor.close()
        conn.close()

def get_site_account(user_id):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT username, api_key FROM user_site_accounts WHERE user_id = %s AND is_active = TRUE LIMIT 1", (user_id,))
        row = cursor.fetchone()
        return row
    finally:
        cursor.close()
        conn.close()

def get_active_site_accounts(user_id):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT username, api_key FROM user_site_accounts WHERE user_id = %s AND is_active = TRUE",
            (user_id,)
        )
        rows = cursor.fetchall()
        return rows  
    finally:
        cursor.close()
        conn.close()

def save_bot(user_id, token):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO user_bots (user_id, token, is_active, expires_at, is_banned) 
        VALUES (%s, %s, 0, CURRENT_TIMESTAMP + INTERVAL '30 days', 0)
        ON CONFLICT (user_id) 
        DO UPDATE SET token = EXCLUDED.token;
    ''', (user_id, token))
    conn.commit()
    cursor.close()
    conn.close()

def add_days_to_user(user_id, days, plan_type=None):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT token FROM user_bots WHERE user_id = %s', (user_id,))
    row = cursor.fetchone()
    if row:
        if plan_type:
            cursor.execute('''
                UPDATE user_bots
                SET expires_at = GREATEST(expires_at, CURRENT_TIMESTAMP) + CAST(%s AS INTERVAL),
                    plan_type = %s
                WHERE user_id = %s
            ''', (f"{days} days", plan_type, user_id))
        else:
            cursor.execute('''
                UPDATE user_bots
                SET expires_at = GREATEST(expires_at, CURRENT_TIMESTAMP) + CAST(%s AS INTERVAL)
                WHERE user_id = %s
            ''', (f"{days} days", user_id))
    else:
        temp_token = f'pending_{user_id}'
        if plan_type:
            cursor.execute('''
                INSERT INTO user_bots (user_id, token, is_active, expires_at, is_banned, plan_type)
                VALUES (%s, %s, 0, CURRENT_TIMESTAMP + CAST(%s AS INTERVAL), 0, %s)
            ''', (user_id, temp_token, f"{days} days", plan_type))
        else:
            cursor.execute('''
                INSERT INTO user_bots (user_id, token, is_active, expires_at, is_banned)
                VALUES (%s, %s, 0, CURRENT_TIMESTAMP + CAST(%s AS INTERVAL), 0)
            ''', (user_id, temp_token, f"{days} days"))
    conn.commit()
    cursor.close()
    conn.close()

def get_user_plan(user_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT plan_type FROM user_bots WHERE user_id = %s", (user_id,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row[0] if row else '1'

def ban_user(user_id, status):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('UPDATE user_bots SET is_banned = %s WHERE user_id = %s', (status, user_id))
    conn.commit()
    cursor.close()
    conn.close()

def set_status(user_id, is_active):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('UPDATE user_bots SET is_active = %s WHERE user_id = %s', (is_active, user_id))
    conn.commit()
    cursor.close()
    conn.close()

def get_bot(user_id):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('SELECT token, is_active, expires_at, is_banned FROM user_bots WHERE user_id = %s', (user_id,))
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        return row
    except Exception:
        cursor.close()
        conn.close()
        return None

def get_all_active_bots():
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('SELECT user_id, token FROM user_bots WHERE is_active = 1 AND is_banned = 0')
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        return rows
    except Exception:
        cursor.close()
        conn.close()
        return []

def get_stats():
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('SELECT COUNT(*) FROM user_bots')
        total = cursor.fetchone()[0]
        cursor.execute('SELECT COUNT(*) FROM user_bots WHERE is_active = 1')
        active = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        return total if total else 0, active if active else 0
    except Exception:
        cursor.close()
        conn.close()
        return 0, 0

def save_hunting_channel(user_id, channel_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO user_hunting_channels (user_id, channel_id)
        VALUES (%s, %s)
        ON CONFLICT (user_id)
        DO UPDATE SET channel_id = EXCLUDED.channel_id;
    ''', (user_id, str(channel_id)))
    conn.commit()
    cursor.close()
    conn.close()

def get_hunting_channel(user_id):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            'SELECT channel_id FROM user_hunting_channels WHERE user_id = %s',
            (user_id,)
        )
        row = cursor.fetchone()
        return row[0] if row else None
    finally:
        cursor.close()
        conn.close()

def set_hunting_status(user_id, is_hunting):
    status_val = 1 if is_hunting else 0
    conn = get_connection()
    cursor = conn.cursor()
    
    # تحديث جدول الحالة المخصص
    cursor.execute('''
        INSERT INTO user_hunting_status (user_id, is_hunting)
        VALUES (%s, %s)
        ON CONFLICT (user_id) DO UPDATE SET is_hunting = EXCLUDED.is_hunting
    ''', (user_id, status_val))
    conn.commit()
    
    # تحديث العمود الجديد داخل جدول البوتات الرئيسي ليتطابق مع الـ HuntingWorker
    try:
        cursor.execute('UPDATE user_bots SET is_hunting = %s WHERE user_id = %s', (status_val, user_id))
        conn.commit()
    except Exception as e:
        logger.warning(f"Could not update is_hunting in user_bots table: {e}")
        conn.rollback()
        
    cursor.close()
    conn.close()

def get_user_countries(user_id):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('SELECT country_name FROM user_countries WHERE user_id = %s', (user_id,))
        rows = cursor.fetchall()
        return [row[0] for row in rows] if rows else []
    finally:
        cursor.close()
        conn.close()

def add_user_country(user_id, country_name):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('''
            INSERT INTO user_countries (user_id, country_name)
            VALUES (%s, %s)
            ON CONFLICT (user_id, country_name) DO NOTHING
        ''', (user_id, country_name))
        conn.commit()
    finally:
        cursor.close()
        conn.close()

def delete_user_country(user_id, country_name):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM user_countries WHERE user_id = %s AND country_name = %s", (user_id, country_name))
        conn.commit()
    finally:
        cursor.close()
        conn.close()

def add_pending_subscription(user_id, plan, payment_method, amount_crypto, wallet_address):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO pending_subscriptions (user_id, plan, payment_method, amount_crypto, wallet_address)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (user_id) DO UPDATE SET
            plan = EXCLUDED.plan,
            payment_method = EXCLUDED.payment_method,
            amount_crypto = EXCLUDED.amount_crypto,
            wallet_address = EXCLUDED.wallet_address,
            created_at = CURRENT_TIMESTAMP
    """, (user_id, plan, payment_method, amount_crypto, wallet_address))
    conn.commit()
    cursor.close()
    conn.close()

def get_pending_subscription(user_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT plan, payment_method, amount_crypto, wallet_address, created_at FROM pending_subscriptions WHERE user_id = %s", (user_id,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row

def delete_pending_subscription(user_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM pending_subscriptions WHERE user_id = %s", (user_id,))
    conn.commit()
    cursor.close()
    conn.close()

def get_all_pending_subscriptions():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, plan, payment_method, amount_crypto, wallet_address, created_at FROM pending_subscriptions ORDER BY created_at DESC")
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows

def get_all_checkers():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, phone, is_active FROM telegram_accounts ORDER BY id")
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows

def delete_checker(account_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM telegram_accounts WHERE id = %s", (account_id,))
    conn.commit()
    cursor.close()
    conn.close()

def toggle_checker(account_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE telegram_accounts SET is_active = NOT is_active WHERE id = %s", (account_id,))
    conn.commit()
    cursor.close()
    conn.close()

def get_account_flood(account_id):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT flood_until FROM telegram_accounts WHERE id=%s", (account_id,))
        row = cursor.fetchone()
        return row[0] if row else None
    finally:
        cursor.close()
        conn.close()

def save_telegram_account(phone, api_id, api_hash, string_session):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO telegram_accounts (phone, api_id, api_hash, string_session)
        VALUES (%s,%s,%s,%s)
        ON CONFLICT (phone) DO UPDATE SET
            api_id=EXCLUDED.api_id,
            api_hash=EXCLUDED.api_hash,
            string_session=EXCLUDED.string_session,
            is_active = TRUE
    """, (phone, api_id, api_hash, string_session))
    conn.commit()
    cursor.close()
    conn.close()

def get_telegram_accounts():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, phone, api_id, api_hash, string_session, is_active,
               flood_until, total_checks, last_used
        FROM telegram_accounts
        ORDER BY id
    """)
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows

def delete_telegram_account(account_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM telegram_accounts WHERE id=%s", (account_id,))
    conn.commit()
    cursor.close()
    conn.close()

def set_account_flood(account_id, flood_until):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE telegram_accounts SET flood_until=%s WHERE id=%s", (flood_until, account_id))
    conn.commit()
    cursor.close()
    conn.close()

def increase_account_checks(account_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE telegram_accounts SET
            total_checks = total_checks + 1,
            last_used = CURRENT_TIMESTAMP
        WHERE id = %s
    """, (account_id,))
    conn.commit()
    cursor.close()
    conn.close()

def get_best_telegram_account():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, phone, api_id, api_hash, string_session
        FROM telegram_accounts
        WHERE is_active = TRUE
          AND (flood_until IS NULL OR flood_until < CURRENT_TIMESTAMP)
        ORDER BY total_checks ASC, last_used ASC NULLS FIRST
        LIMIT 1
    """)
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row

def log_activity(user_id, action, details=""):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO activity_log (user_id, action, details) VALUES (%s, %s, %s)",
                   (user_id, action, details))
    conn.commit()
    cursor.close()
    conn.close()

def get_recent_activities(limit=50):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, user_id, action, details, created_at FROM activity_log ORDER BY created_at DESC LIMIT %s", (limit,))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows

def create_ticket(user_id, subject, message):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO support_tickets (user_id, subject, message) VALUES (%s, %s, %s)",
                   (user_id, subject, message))
    conn.commit()
    cursor.close()
    conn.close()

def get_open_tickets():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, user_id, subject, message, status, admin_reply, created_at FROM support_tickets WHERE status='open' ORDER BY created_at DESC")
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows

def get_all_tickets():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, user_id, subject, message, status, admin_reply, created_at FROM support_tickets ORDER BY created_at DESC")
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows

def reply_ticket(ticket_id, reply_text):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE support_tickets SET admin_reply = %s, status = 'closed', updated_at = CURRENT_TIMESTAMP WHERE id = %s",
                   (reply_text, ticket_id))
    conn.commit()
    cursor.close()
    conn.close()

def close_ticket(ticket_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE support_tickets SET status = 'closed', updated_at = CURRENT_TIMESTAMP WHERE id = %s", (ticket_id,))
    conn.commit()
    cursor.close()
    conn.close()

def get_setting(key, default=None):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = %s", (key,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    if row:
        return row[0]
    return default

def set_setting(key, value):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO settings (key, value) VALUES (%s, %s)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
    """, (key, str(value)))
    conn.commit()
    cursor.close()
    conn.close()

def get_all_settings():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT key, value FROM settings ORDER BY key")
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows

def update_country_settings(user_id, country_code, number_type=None, session_status=None):
    conn = get_connection()
    cursor = conn.cursor()
    if number_type is not None:
        cursor.execute("UPDATE user_countries SET number_type = %s WHERE user_id = %s AND country_name = %s",
                       (number_type, user_id, country_code))
    if session_status is not None:
        cursor.execute("UPDATE user_countries SET session_status = %s WHERE user_id = %s AND country_name = %s",
                       (session_status, user_id, country_code))
    conn.commit()
    cursor.close()
    conn.close()

def get_country_settings(user_id, country_code):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT number_type, session_status FROM user_countries WHERE user_id = %s AND country_name = %s",
                   (user_id, country_code))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    if row:
        return {"number_type": row[0] or "all", "session_status": row[1] or "all"}
    return {"number_type": "all", "session_status": "all"}

def insert_pending_report(user_id, username, phone_number, country_code, status_text, status_type):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO pending_reports (user_id, username, phone_number, country_code, status_text, status_type)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (user_id, username, phone_number, country_code, status_text, status_type))
    conn.commit()
    cursor.close()
    conn.close()

def get_unsent_reports_for_user(user_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, username, phone_number, country_code, status_text, status_type 
        FROM pending_reports 
        WHERE user_id = %s AND is_sent = FALSE
        ORDER BY id ASC
    """, (user_id,))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows

def mark_report_as_sent(report_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE pending_reports SET is_sent = TRUE WHERE id = %s
    """, (report_id,))
    conn.commit()
    cursor.close()
    conn.close()

def init_reports_table():
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pending_reports (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                username VARCHAR(255) NOT NULL,
                phone_number VARCHAR(50) NOT NULL,
                country_code VARCHAR(20) NOT NULL,
                status_text TEXT NOT NULL,
                status_type VARCHAR(50) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_sent BOOLEAN DEFAULT FALSE
            )
        """)
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        logger.error(f"⚠️ خطأ أثناء تهيئة جدول التقارير: {e}")

# استدعاء تلقائي آمن للجدول المستقل
init_reports_table()
