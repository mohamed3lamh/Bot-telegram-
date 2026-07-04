from database import get_connection
from datetime import datetime, timezone

class AccountManager:
    def __init__(self):
        pass

    async def get_all_accounts(self):
        """ جلب جميع حسابات تيليجرام المفعلة. """
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, api_id, api_hash, string_session, is_active, flood_until
            FROM telegram_accounts
            WHERE is_active = TRUE
            ORDER BY id ASC
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        
        accounts = []
        for row in rows:
            accounts.append({
                "id": row[0],
                "api_id": row[1],
                "api_hash": row[2],
                "session": row[3],
                "is_active": row[4],
                "flood_until": row[5]
            })
        return accounts

    async def get_available_account(self):
        """ يرجع أول حساب صالح للاستخدام (ليس معطل وليس داخل FloodWait). """
        accounts = await self.get_all_accounts()
        if not accounts:
            return None
            
        now = datetime.now(timezone.utc)
        for account in accounts:
            flood_until = account["flood_until"]
            # إذا الحساب غير داخل FloodWait
            if flood_until is None:
                return account
            # انتهى وقت الـ Flood
            if flood_until <= now:
                return account
        return None

    # 🚀 الدالة المضافة لحل مشكلة الـ Logs وتطابق الأسماء
    async def get_available_accounts(self):
        """ دالة إضافية بالصيغة الجمع لتفادي خطأ AttributeError في نظام الصيد """
        accounts = await self.get_all_accounts()
        if not accounts:
            return []
            
        now = datetime.now(timezone.utc)
        available = []
        for account in accounts:
            flood_until = account["flood_until"]
            if flood_until is None or flood_until <= now:
                available.append(account)
        return available

    async def disable_account(self, account_id):
        """ تعطيل الحساب. """
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            UPDATE telegram_accounts SET is_active = FALSE WHERE id=%s
        """, (account_id,))
        conn.commit()
        cur.close()
        conn.close()

    async def enable_account(self, account_id):
        """ إعادة تفعيل الحساب. """
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            UPDATE telegram_accounts SET is_active = TRUE WHERE id=%s
        """, (account_id,))
        conn.commit()
        cur.close()
        conn.close()

account_manager = AccountManager()
