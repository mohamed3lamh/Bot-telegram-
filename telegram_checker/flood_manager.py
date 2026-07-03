from datetime import datetime, timedelta, timezone
import database
import asyncio

class FloodManager:

    async def is_flooded(self, account_id):
        """
        التحقق من حالة الحظر المؤقت (FloodWait).
        """
        flood_until = await asyncio.to_thread(database.get_account_flood, account_id)
        if not flood_until:
            return False
            
        # التأكد من مطابقة المنطقة الزمنية لتجنب TypeError: can't compare offset-naive and offset-aware datetimes
        if flood_until.tzinfo is None:
            flood_until = flood_until.replace(tzinfo=timezone.utc)
            
        return datetime.now(timezone.utc) < flood_until

    async def set_flood(self, account_id, seconds):
        """
        عند دخول الحساب FloodWait.
        """
        flood_until = datetime.now(timezone.utc) + timedelta(seconds=seconds)
        await asyncio.to_thread(database.set_account_flood, account_id, flood_until)

    async def account_used(self, account_id):
        """
        زيادة عداد الفحص.
        """
        await asyncio.to_thread(database.increase_account_checks, account_id)

    async def account_ok(self, account_id):
        """
        حالياً لا نحتاج أي شيء هنا.
        """
        return True


flood_manager = FloodManager()
