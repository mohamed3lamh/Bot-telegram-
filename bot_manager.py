import asyncio
import logging
from telegram import Bot
from telegram.ext import Application
from user_bot import create_user_app
import database as db

logger = logging.getLogger(__name__)

class BotManager:
    def __init__(self):
        self.registry = {} # {token: {"app": Application, "lock": asyncio.Lock(), "state": "STOPPED"}}

    def _ensure_token_entry(self, token):
        if token not in self.registry:
            self.registry[token] = {"app": None, "lock": asyncio.Lock(), "state": "STOPPED"}

    async def start_bot(self, user_id, token):
        self._ensure_token_entry(token)
        async with self.registry[token]["lock"]:
            if self.registry[token]["state"] == "RUNNING":
                logger.info(f"ℹ️ البوت {token[:10]} يعمل بالفعل.")
                return False

            try:
                self.registry[token]["state"] = "STARTING"
                app = create_user_app(token)
                await app.initialize()
                await app.start()
                await app.updater.start_polling()
                
                self.registry[token]["app"] = app
                self.registry[token]["state"] = "RUNNING"
                logger.info(f"🚀 تم تشغيل بوت المستخدم {user_id}")
                return True
            except Exception as e:
                self.registry[token]["state"] = "STOPPED"
                logger.error(f"❌ فشل تشغيل بوت {user_id}: {e}")
                return False

    async def stop_bot(self, token):
        self._ensure_token_entry(token)
        async with self.registry[token]["lock"]:
            if self.registry[token]["state"] != "RUNNING":
                return

            try:
                self.registry[token]["state"] = "STOPPING"
                app = self.registry[token]["app"]
                await app.updater.stop()
                await app.stop()
                await app.shutdown()
                self.registry[token]["state"] = "STOPPED"
                logger.info(f"🛑 تم إيقاف البوت {token[:10]}")
            except Exception as e:
                logger.error(f"❌ خطأ أثناء إيقاف البوت: {e}")

    async def restore_active_bots(self):
        """استعادة كافة البوتات النشطة بأمان"""
        try:
            active_bots = await db.get_all_active_bots()
            logger.info(f"جاري استعادة {len(active_bots)} من البوتات النشطة...")
            for user_id, token in active_bots:
                # تشغيل آمن
                asyncio.create_task(self.start_bot(user_id, token))
        except Exception as e:
            logger.error(f"Error restoring bots: {e}")

bot_manager = BotManager()
