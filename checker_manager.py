import asyncio
import logging
import os
from telethon import TelegramClient
from telethon.errors import (
    PhoneNumberBannedError,
    PhoneNumberFloodError,
    PhonePasswordProtectedError,
    PhoneMigrateError,
    FloodWaitError,
)
import database as db

logger = logging.getLogger(__name__)

SESSION_DIR = "checker_sessions"

# عناوين DCs الثابتة — لا تعتمد على GetConfigRequest لأنها ترجع عناوين خطأ أحياناً
DC_ADDRESSES = {
    1: ("149.154.175.10", 443),
    2: ("149.154.167.51", 443),
    3: ("149.154.175.100", 443),
    4: ("149.154.167.92", 443),
    5: ("149.154.175.50", 443),
}

class CheckerAccountClient:
    def __init__(self, db_id, api_id, api_hash, phone, is_active, is_limited):
        self.db_id = db_id
        self.api_id = api_id
        self.api_hash = api_hash
        self.phone = phone
        self.is_active = is_active
        self.is_limited = is_limited
        self.client = None
        self.connected = False
        self.total_checked = 0
        self.flood_errors = 0

class CheckerManager:
    def __init__(self):
        self.accounts: dict[int, CheckerAccountClient] = {}
        self._counter = 0
        os.makedirs(SESSION_DIR, exist_ok=True)

    def load_from_db(self):
        rows = db.get_all_checker_accounts()
        db_ids = {r[0] for r in rows}
        # Remove deleted accounts
        for acc_id in list(self.accounts.keys()):
            if acc_id not in db_ids:
                self.accounts.pop(acc_id)
        # Add/update existing
        for row in rows:
            acc_id, api_id, api_hash, phone, is_active, is_limited = row
            if acc_id not in self.accounts:
                self.accounts[acc_id] = CheckerAccountClient(
                    acc_id, api_id, api_hash, phone, is_active, is_limited
                )
            else:
                acc = self.accounts[acc_id]
                acc.api_id = api_id
                acc.api_hash = api_hash
                acc.phone = phone
                acc.is_active = is_active
                acc.is_limited = is_limited

    async def start_all(self):
        for acc in self.accounts.values():
            if acc.is_active and not acc.is_limited:
                await self._start_client(acc)

    async def _start_client(self, acc):
        try:
            session_path = os.path.join(SESSION_DIR, f"checker_{acc.db_id}")
            acc.client = TelegramClient(session_path, acc.api_id, acc.api_hash)
            await acc.client.connect()
            if not await acc.client.is_user_authorized():
                logger.warning(f"Checker #{acc.db_id} not authorized on current DC, scanning DCs 1-5...")
                auth_ok = False
                for dc_id in range(1, 6):
                    addr = DC_ADDRESSES.get(dc_id)
                    if not addr:
                        continue
                    ip, port = addr
                    try:
                        acc.client.session.set_dc(dc_id, ip, port)
                        await acc.client.disconnect()
                        await acc.client.connect()
                        if await acc.client.is_user_authorized():
                            auth_ok = True
                            logger.info(f"Checker #{acc.db_id} authorized on DC {dc_id}")
                            break
                    except Exception:
                        continue
                if not auth_ok:
                    logger.warning(f"Checker account #{acc.db_id} not authorized on any DC, skipping")
                    await acc.client.disconnect()
                    acc.client = None
                    return
            acc.connected = True
            logger.info(f"Checker account #{acc.db_id} ({acc.phone}) connected successfully on DC {acc.client.session._dc_id}")
        except Exception as e:
            logger.error(f"Failed to start checker account #{acc.db_id}: {e}")

    async def stop_all(self):
        for acc in self.accounts.values():
            if acc.client and acc.connected:
                try:
                    await acc.client.disconnect()
                except Exception as e:
                    logger.warning(f"Error disconnecting checker #{acc.db_id}: {e}")

    async def restart_account(self, acc_id: int):
        acc = self.accounts.get(acc_id)
        if not acc:
            return
        if acc.client and acc.connected:
            try:
                await acc.client.disconnect()
            except Exception:
                pass
        acc.connected = False
        acc.flood_errors = 0
        if acc.is_active and not acc.is_limited:
            await self._start_client(acc)

    async def check_number(self, phone_number: str) -> str:
        acc = self._get_next_account()
        if not acc:
            logger.warning("No active checker accounts available")
            return "unknown"

        logger.info(f"Checking {phone_number} via checker account #{acc.db_id}")
        try:
            await acc.client.send_code_request(phone_number)
            acc.total_checked += 1
            logger.info(f"Result for {phone_number}: unknown (send_code succeeded but not in registered criteria)")
            return "unknown"
        except PhoneNumberBannedError:
            acc.total_checked += 1
            logger.info(f"Result for {phone_number}: banned")
            return "banned"
        except PhoneNumberFloodError:
            acc.total_checked += 1
            logger.info(f"Result for {phone_number}: registered (flood on target number)")
            return "registered"
        except PhonePasswordProtectedError:
            acc.total_checked += 1
            logger.info(f"Result for {phone_number}: registered (password protected)")
            return "registered"
        except PhoneMigrateError as e:
            logger.warning(f"PhoneMigrateError for {phone_number}, new_dc={e.new_dc}, migrating DC...")
            try:
                addr = DC_ADDRESSES.get(e.new_dc)
                if addr:
                    ip, port = addr
                    acc.client.session.set_dc(e.new_dc, ip, port)
                await acc.client.disconnect()
                await acc.client.connect()
                await acc.client.send_code_request(phone_number)
                return "unknown"
            except PhoneNumberBannedError:
                return "banned"
            except PhoneNumberFloodError:
                return "registered"
            except PhonePasswordProtectedError:
                return "registered"
            except Exception as e2:
                logger.warning(f"Retry after PhoneMigrateError failed for {phone_number}: {e2}")
                return "unknown"
        except FloodWaitError as e:
            acc.flood_errors += 1
            logger.warning(f"Checker #{acc.db_id} flood wait {e.seconds}s (error #{acc.flood_errors})")
            if acc.flood_errors >= 5:
                db.set_checker_account_limited(acc.db_id, True)
                acc.is_limited = True
                acc.is_active = False
                logger.warning(f"Checker account #{acc.db_id} marked as limited due to flood")
            return "unknown"
        except (asyncio.TimeoutError, TimeoutError) as e:
            logger.warning(f"Result for {phone_number}: unknown (timeout: {e})")
            return "unknown"
        except Exception as e:
            logger.warning(f"Result for {phone_number}: unknown ({type(e).__name__}: {e})")
            return "unknown"

    def _get_next_account(self):
        active = [a for a in self.accounts.values()
                  if a.is_active and not a.is_limited and a.connected]
        if not active:
            return None
        idx = self._counter % len(active)
        self._counter += 1
        return active[idx]

    async def add_account(self, api_id, api_hash, phone, session_source_path=None):
        db.add_checker_account(api_id, api_hash, phone)
        self.load_from_db()
        # العثور على الحساب المضاف حديثاً
        for acc in self.accounts.values():
            if acc.phone == phone and acc.api_id == api_id:
                if session_source_path:
                    # نسخ الجلسة المصرح بها من عملية الإعداد
                    import shutil
                    dest = os.path.join(SESSION_DIR, f"checker_{acc.db_id}.session")
                    try:
                        shutil.copy2(session_source_path, dest)
                    except Exception as e:
                        logger.warning(f"Failed to copy session for account #{acc.db_id}: {e}")
                        break
                await self._start_client(acc)
                break

    async def remove_account(self, acc_id: int):
        acc = self.accounts.get(acc_id)
        if acc and acc.client:
            try:
                await acc.client.disconnect()
            except Exception:
                pass
        db.delete_checker_account(acc_id)
        self.accounts.pop(acc_id, None)

    async def toggle_account(self, acc_id: int):
        db.toggle_checker_account(acc_id)
        self.load_from_db()
        acc = self.accounts.get(acc_id)
        if acc:
            if acc.is_active and not acc.is_limited:
                await self._start_client(acc)
            else:
                if acc.client and acc.connected:
                    try:
                        await acc.client.disconnect()
                    except Exception:
                        pass
                    acc.connected = False

    def get_status_text(self, acc_id: int) -> str:
        acc = self.accounts.get(acc_id)
        if not acc:
            return "❓ غير موجود"
        if not acc.is_active:
            return "🔴 معطل"
        if acc.is_limited:
            return "⛔ محدود"
        if not acc.connected:
            return "🟡 غير متصل"
        return "🟢 متصل"

    def get_total_checked(self, acc_id: int) -> int:
        acc = self.accounts.get(acc_id)
        return acc.total_checked if acc else 0


checker_manager = CheckerManager()
