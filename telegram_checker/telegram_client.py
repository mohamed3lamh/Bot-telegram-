import asyncio
import logging
from telethon import TelegramClient
from telethon.sessions import StringSession

logger = logging.getLogger(__name__)


class SessionUnauthorizedError(Exception):
    """Raised when the Telegram session is not authorized."""
    pass


class TelegramClientManager:
    """
    يدير اتصالات Telethon مع ضمان:
    - قفل واحد لكل حساب يغطي العملية بالكامل (get_client + send_code_request)
    - لا توجد عمليتان متزامنتان على نفس الجلسة أبداً
    - تنظيف صحيح للاتصالات المعطوبة
    - Exponential Backoff عند إعادة الاتصال لتجنب الضغط على الشبكة
    """

    # ── إعدادات Backoff ──────────────────────────────────────
    _RECONNECT_BASE_DELAY = 1.0   # ثانية — التأخير الأولي
    _RECONNECT_MAX_DELAY  = 30.0  # ثانية — الحد الأقصى للتأخير
    _RECONNECT_MAX_TRIES  = 4     # عدد المحاولات قبل الاستسلام المؤقت
    # ─────────────────────────────────────────────────────────

    def __init__(self):
        self.clients  = {}   # {account_id: TelegramClient}
        self._locks   = {}   # {account_id: asyncio.Lock} — قفل واحد لكل حساب
        self._backoff = {}   # {account_id: int} — عداد الفشل المتراكم لكل حساب

    # ─────────────────────────────── Locks ───────────────────
    def get_account_lock(self, account_id: int) -> asyncio.Lock:
        """
        يُرجع القفل الخاص بالحساب.
        يُستخدم في check_phone لتغطية العملية بالكامل.
        """
        if account_id not in self._locks:
            self._locks[account_id] = asyncio.Lock()
        return self._locks[account_id]

    # ─────────────────────────────── Client ──────────────────
    async def get_client(self, account: dict) -> TelegramClient:
        """
        يُرجع عميل Telethon متصل وموثّق.
        ⚠️ يجب استدعاؤها دائماً من داخل get_account_lock لتجنب Race Conditions.

        التحسينات:
        - Exponential Backoff عند إعادة الاتصال
        - إعادة ضبط عداد الفشل عند النجاح
        """
        account_id = account["id"]

        # --- محاولة استخدام الاتصال المخزن ---
        if account_id in self.clients:
            client = self.clients[account_id]
            try:
                if not client.is_connected():
                    logger.info(f"[ClientManager] #{account_id}: الاتصال منقطع، إعادة اتصال...")
                    await asyncio.wait_for(client.connect(), timeout=15.0)

                if await asyncio.wait_for(client.is_user_authorized(), timeout=15.0):
                    logger.info(f"[ClientManager] #{account_id}: استُعيد الاتصال المخزن ✅")
                    self._backoff[account_id] = 0  # إعادة ضبط عداد الفشل
                    return client
                else:
                    logger.warning(f"[ClientManager] #{account_id}: الجلسة المخزنة غير مخوّلة.")
                    raise SessionUnauthorizedError("Cached session is no longer authorized.")

            except SessionUnauthorizedError:
                raise
            except Exception as e:
                logger.warning(
                    f"[ClientManager] #{account_id}: فشل التحقق من الاتصال المخزن: {e}. "
                    f"جاري التنظيف..."
                )
            finally:
                if account_id in self.clients and self.clients.get(account_id) is client:
                    try:
                        await asyncio.wait_for(client.disconnect(), timeout=5.0)
                    except Exception:
                        pass
                    self.clients.pop(account_id, None)

        # --- Exponential Backoff قبل الإنشاء الجديد ---
        fail_count = self._backoff.get(account_id, 0)
        if fail_count > 0:
            delay = min(
                self._RECONNECT_BASE_DELAY * (2 ** (fail_count - 1)),
                self._RECONNECT_MAX_DELAY,
            )
            logger.info(
                f"[ClientManager] #{account_id}: Backoff — انتظار {delay:.1f}s "
                f"(محاولة #{fail_count + 1})"
            )
            await asyncio.sleep(delay)

        if fail_count >= self._RECONNECT_MAX_TRIES:
            logger.error(
                f"[ClientManager] #{account_id}: تجاوز الحد الأقصى للمحاولات "
                f"({self._RECONNECT_MAX_TRIES}) — استسلام مؤقت."
            )
            raise Exception(
                f"[ClientManager] #{account_id}: فشل الاتصال بعد "
                f"{self._RECONNECT_MAX_TRIES} محاولات."
            )

        # --- إنشاء اتصال جديد ---
        logger.info(f"[ClientManager] #{account_id}: إنشاء اتصال Telethon جديد...")
        new_client = TelegramClient(
            StringSession(account["session"]),
            int(account["api_id"]),
            account["api_hash"]
        )

        try:
            await asyncio.wait_for(new_client.connect(), timeout=15.0)

            if not await asyncio.wait_for(new_client.is_user_authorized(), timeout=15.0):
                await asyncio.wait_for(new_client.disconnect(), timeout=5.0)
                raise SessionUnauthorizedError(
                    f"New session for account #{account_id} is not authorized."
                )

            logger.info(f"[ClientManager] #{account_id}: اتصال جديد ناجح ✅")
            self.clients[account_id] = new_client
            self._backoff[account_id] = 0  # إعادة ضبط عداد الفشل
            return new_client

        except Exception as e:
            self._backoff[account_id] = fail_count + 1
            try:
                await asyncio.wait_for(new_client.disconnect(), timeout=5.0)
            except Exception:
                pass
            if isinstance(e, SessionUnauthorizedError):
                raise
            raise Exception(f"[ClientManager] #{account_id}: فشل إنشاء الاتصال: {e}")

    # ─────────────────────────────── Disconnect ──────────────
    async def disconnect_client(self, account_id: int):
        """فصل اتصال حساب معين وتنظيفه من الذاكرة."""
        client = self.clients.pop(account_id, None)
        if client is None:
            return
        try:
            await asyncio.wait_for(client.disconnect(), timeout=5.0)
            logger.info(f"[ClientManager] #{account_id}: تم الفصل ✅")
        except Exception as e:
            logger.warning(f"[ClientManager] #{account_id}: خطأ أثناء الفصل: {e}")

    def reset_backoff(self, account_id: int):
        """إعادة ضبط عداد فشل الاتصال يدوياً (مثلاً بعد انتهاء FloodWait)."""
        self._backoff.pop(account_id, None)

    async def disconnect_all(self):
        """فصل جميع الاتصالات وتنظيف الذاكرة."""
        for account_id in list(self.clients.keys()):
            await self.disconnect_client(account_id)
        self.clients.clear()
        self._backoff.clear()


telegram_client_manager = TelegramClientManager()
