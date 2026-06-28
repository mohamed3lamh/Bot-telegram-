import asyncio
import logging
from telegram import Bot
from telegram.error import InvalidToken, TelegramError
from user_bot import create_user_app
import database as db

logger = logging.getLogger(__name__)

class BotManager:
    def __init__(self):
        self.running_bots = {}

    async def validate_token(self, token):
        try:
            bot = Bot(token)
            await bot.get_me()
            return True
        except Exception:
            return False

    async def start_bot(self, user_id, token):
        try:
            app = create_user_app(token)
            await app.initialize()
            await app.start()
            await app.updater.start_polling()
            self.running_bots[user_id] = app
            logger.info(f"🚀 تم تشغيل بوت المستخدم {user_id}")
        except Exception as e:
            logger.error(f"❌ فشل تشغيل بوت {user_id}: {e}")

    async def stop_bot(self, user_id):
        if user_id in self.running_bots:
            app = self.running_bots[user_id]
            await app.updater.stop()
            await app.stop()
            await app.shutdown()
            del self.running_bots[user_id]
            logger.info(f"🛑 تم إيقاف بوت المستخدم {user_id}")

    async def restore_active_bots(self):
        """استعادة كافة البوتات التي كانت تعمل قبل إعادة تشغيل السيرفر"""
        try:
            active_bots = await db.get_all_active_bots()
            logger.info(f"جاري استعادة {len(active_bots)} من البوتات النشطة...")
            for user_id, token in active_bots:
                # تشغيل كل بوت في مهمة مستقلة منفصلة
                asyncio.create_task(self.start_bot(user_id, token))
        except Exception as e:
            logger.error(f"Error restoring bots: {e}")

bot_manager = BotManager()
