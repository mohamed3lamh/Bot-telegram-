from telethon import TelegramClient
from telethon.sessions import StringSession


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
    _RECONNECT_MAX_TRIES  = 4     # عدد المحاولات قبل الاستسلام
    # ─────────────────────────────────────────────────────────

    def __init__(self):
        self.clients = {}        # {account_id: TelegramClient}
        self._locks  = {}        # {account_id: asyncio.Lock} — قفل واحد لكل حساب
        self._backoff = {}       # {account_id: int} — عداد الفشل المتراكم لكل حساب

    # ─────────────────────────────── Locks ───────────────────
    def get_account_lock(self, account_id: int) -> asyncio.Lock:
        """
        account = {
            "id": 1,
            "api_id": 12345,
            "api_hash": "xxxxx",
            "session": "xxxxx"
        }
        """

    # ─────────────────────────────── Client ──────────────────
    async def get_client(self, account: dict) -> TelegramClient:
        """
        يُرجع عميل Telethon متصل وموثّق.
        ⚠️ يجب استدعاء هذه الدالة دائماً من داخل get_account_lock المُكتسب في check_phone.
        لا تحتوي هذه الدالة على قفل داخلي لتجنب الـ Deadlock.

        التحسينات:
        - Exponential Backoff عند إعادة الاتصال بدلاً من الإنشاء الفوري
        - إعادة ضبط عداد الفشل عند النجاح
        """
        account_id = account["id"]

        if account_id in self.clients:
            client = self.clients[account_id]

            try:
                if not client.is_connected():
                    await client.connect()

                if await asyncio.wait_for(client.is_user_authorized(), timeout=15.0):
                    logger.info(f"[ClientManager] #{account_id}: استُعيد الاتصال المخزن ✅")
                    self._backoff[account_id] = 0  # إعادة ضبط عداد الفشل
                    return client
            except Exception:
                pass

            except SessionUnauthorizedError:
                raise  # نُعيد رفعها بدون تغيير
            except Exception as e:
                logger.warning(
                    f"[ClientManager] #{account_id}: فشل التحقق من الاتصال المخزن: {e}. "
                    f"جاري التنظيف..."
                )
            finally:
                # تنظيف الاتصال المعطوب في جميع حالات الفشل
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
                f"قبل الإنشاء (محاولة #{fail_count + 1})"
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

        await client.connect()

        if not await client.is_user_authorized():
            await client.disconnect()
            raise SessionUnauthorizedError(
                "Telegram session is not authorized."
            )

            logger.info(f"[ClientManager] #{account_id}: اتصال جديد ناجح ✅")
            self.clients[account_id] = new_client
            self._backoff[account_id] = 0  # إعادة ضبط عداد الفشل
            return new_client

        except Exception as e:
            # زيادة عداد الفشل
            self._backoff[account_id] = fail_count + 1
            # تنظيف الاتصال الجديد الفاشل
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
            await client.disconnect()
        except Exception:
            pass

    def reset_backoff(self, account_id: int):
        """إعادة ضبط عداد فشل الاتصال يدوياً (مثلاً بعد انتهاء FloodWait)."""
        self._backoff.pop(account_id, None)

    async def disconnect_all(self):
        for client in list(self.clients.values()):
            try:
                await client.disconnect()
            except Exception:
                pass

        self.clients.clear()
        self._backoff.clear()


telegram_client_manager = TelegramClientManager()
