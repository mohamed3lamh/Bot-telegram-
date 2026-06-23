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

    def column_exists(table, column):
        cursor.execute(f"SELECT COUNT(*) FROM information_schema.columns WHERE table_name='{table}' AND column_name='{column}'")
        return cursor.fetchone()[0] > 0

    if not column_exists('user_bots', 'expires_at'):
        cursor.execute("ALTER TABLE user_bots ADD COLUMN expires_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP + INTERVAL '30 days'")
        conn.commit()
    if not column_exists('user_bots', 'is_banned'):
        cursor.execute("ALTER TABLE user_bots ADD COLUMN is_banned INTEGER DEFAULT 0")
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
        # لم نعد نعطل الحسابات الأخرى، فقط نضيف/نحدث هذا الحساب ونجعله نشطًا
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
    """تبديل حالة حساب واحد (نشط/غير نشط) دون التأثير على باقي الحسابات"""
    conn = get_connection()
    cursor = conn.cursor()
    try:
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
    """الحساب النشط فقط"""
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
    """استرجاع جميع حسابات الموقع النشطة للمستخدم"""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT username, api_key FROM user_site_accounts WHERE user_id = %s AND is_active = TRUE",
            (user_id,)
        )
        rows = cursor.fetchall()
        return rows  # list of (username, api_key)
    finally:
        cursor.close()
        conn.close()

# --- باقي الدوال (موجودة مسبقاً) ---
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

def add_days_to_user(user_id, days):
    conn = get_connection()
    cursor = conn.cursor()
    # إنشاء سجل افتراضي إذا لم يكن موجودًا، ثم إضافة الأيام
    cursor.execute('''
        INSERT INTO user_bots (user_id, token, is_active, expires_at, is_banned)
        VALUES (%s, '', 0, CURRENT_TIMESTAMP + CAST(%s AS INTERVAL), 0)
        ON CONFLICT (user_id) DO UPDATE
        SET expires_at = GREATEST(user_bots.expires_at, CURRENT_TIMESTAMP) + CAST(%s AS INTERVAL)
    ''', (user_id, f"{days} days", f"{days} days"))
    conn.commit()
    cursor.close()
    conn.close()

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
    cursor.execute('''
        INSERT INTO user_hunting_status (user_id, is_hunting)
        VALUES (%s, %s)
        ON CONFLICT (user_id) DO UPDATE SET is_hunting = EXCLUDED.is_hunting
    ''', (user_id, status_val))
    conn.commit()
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

# ---------- نظام الاشتراكات المعلقة ----------
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
    """حذف حساب فاحص نهائيًا من قاعدة البيانات"""
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
