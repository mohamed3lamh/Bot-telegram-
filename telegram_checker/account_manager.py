from database import get_connection
from datetime import datetime, timezone
import time
import asyncio

class AccountManager:
    def __init__(self):
        self._accounts_cache = None
        self._accounts_cache_ts = 0
        self._ACCOUNTS_CACHE_TTL = 15  # ثانية

    async def get_all_accounts(self):
        """ جلب جميع حسابات تيليجرام المفعلة (مع كاش 15 ثانية). """
        now = time.monotonic()
        if self._accounts_cache is not None and (now - self._accounts_cache_ts) < self._ACCOUNTS_CACHE_TTL:
            return self._accounts_cache

        def _fetch():
            with get_connection() as conn:
                cur = conn.cursor()
                try:
                    cur.execute("""
                        SELECT id, api_id, api_hash, string_session, is_active, flood_until
                        FROM telegram_accounts
                        WHERE is_active = TRUE
                        ORDER BY id ASC
                    """)
                    return cur.fetchall()
                finally:
                    cur.close()

        rows = await asyncio.to_thread(_fetch)
        
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

        self._accounts_cache = accounts
        self._accounts_cache_ts = now
        return accounts

    def invalidate_accounts_cache(self):
        """ إبطال الكاش فوراً (يُستخدم عند تعطيل حساب أو تغيير حالته). """
        self._accounts_cache = None
        self._accounts_cache_ts = 0

    async def get_available_account(self):
        """ يرجع أول حساب صالح للاستخدام (ليس معطل وليس داخل FloodWait). """
        accounts = await self.get_all_accounts()
        if not accounts:
            return None
            
        now = datetime.now(timezone.utc)
        for account in accounts:
            flood_until = account["flood_until"]
            # التأكد من مطابقة المنطقة الزمنية لتجنب TypeError: can't compare offset-naive and offset-aware datetimes
            if flood_until is not None and flood_until.tzinfo is None:
                flood_until = flood_until.replace(tzinfo=timezone.utc)
                
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
            # التأكد من مطابقة المنطقة الزمنية لتجنب TypeError
            if flood_until is not None and flood_until.tzinfo is None:
                flood_until = flood_until.replace(tzinfo=timezone.utc)
                
            if flood_until is None or flood_until <= now:
                available.append(account)
        return available

    async def disable_account(self, account_id):
        """ تعطيل الحساب. """
        def _disable():
            with get_connection() as conn:
                cur = conn.cursor()
                try:
                    cur.execute("""
                        UPDATE telegram_accounts SET is_active = FALSE WHERE id=%s
                    """, (account_id,))
                    conn.commit()
                finally:
                    cur.close()
        await asyncio.to_thread(_disable)
        self.invalidate_accounts_cache()  # إبطال الكاش فوراً

    async def enable_account(self, account_id):
        """ إعادة تفعيل الحساب. """
        def _enable():
            with get_connection() as conn:
                cur = conn.cursor()
                try:
                    cur.execute("""
                        UPDATE telegram_accounts SET is_active = TRUE WHERE id=%s
                    """, (account_id,))
                    conn.commit()
                finally:
                    cur.close()
        await asyncio.to_thread(_enable)
        self.invalidate_accounts_cache()  # إبطال الكاش فوراً

account_manager = AccountManager()
