import asyncio
import logging

logger = logging.getLogger(__name__)

class QueueManager:
    def __init__(self):
        self._queue = asyncio.Queue()
        self._tracked_phones = set()

    async def add_to_queue(self, phone: str):
        clean_phone = phone.strip().replace("+", "")
        if clean_phone not in self._tracked_phones:
            self._tracked_phones.add(clean_phone)
            await self._queue.put(clean_phone)
            logger.info(f"📍 تم إضافة الرقم {clean_phone} إلى طابور الفحص تلقائياً.")
            return True
        return False

    async def get_next_phone(self) -> str:
        phone = await self._queue.get()
        if phone in self._tracked_phones:
            self._tracked_phones.remove(phone)
        return phone

    def is_empty(self) -> bool:
        return self._queue.empty()

    def queue_size(self) -> int:
        return self._queue.qsize()

    def clear_queue(self):
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        self._tracked_phones.clear()
        logger.info("🗑️ تم تفريغ طابور فحص الأرقام بالكامل.")

# تصدير الاثنين معاً (الكبير والصغير) لتفادي أخطاء الاستدعاء أياً كان نوعها
QueueManager = QueueManager
queue_manager = QueueManager()
