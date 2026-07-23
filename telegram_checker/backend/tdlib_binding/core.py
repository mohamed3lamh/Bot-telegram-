import ctypes
import json
import asyncio
import threading
import uuid
import logging
import os
import platform
from typing import Dict, Any, Callable, Optional

logger = logging.getLogger(__name__)

class TDLibBindingException(Exception):
    """استثناء خاص بطبقة الربط."""
    pass

def _get_bundled_lib_path() -> str:
    try:
        import telegram
        base_dir = os.path.dirname(telegram.__file__)
        sys_name = platform.system().lower()
        if sys_name == 'linux':
            lib_path = os.path.join(base_dir, 'lib', 'linux', 'libtdjson.so')
        elif sys_name == 'darwin':
            lib_path = os.path.join(base_dir, 'lib', 'darwin', 'libtdjson.dylib')
        else:
            lib_path = os.path.join(base_dir, 'lib', 'windows', 'tdjson.dll')
            
        if os.path.exists(lib_path):
            return lib_path
    except Exception:
        pass
    return "libtdjson.so"

class TDLibClient:
    """
    مُغلف (Wrapper) مستقل تماماً وغير متزامن (Async) لمكتبة libtdjson.so.
    لا يحتوي على أي منطق خاص بالمشروع (Auth/Session/Proxy)، فقط إرسال واستقبال JSON.
    """
    def __init__(self, lib_path: str = None):
        if lib_path is None:
            lib_path = _get_bundled_lib_path()
        try:
            self.lib = ctypes.CDLL(lib_path)
        except OSError as e:
            raise TDLibBindingException(f"Failed to load TDLib from {lib_path}: {e}")

        # تعريف تواقيع دوال C
        self.lib.td_json_client_create.restype = ctypes.c_void_p
        self.lib.td_json_client_send.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        self.lib.td_json_client_receive.argtypes = [ctypes.c_void_p, ctypes.c_double]
        self.lib.td_json_client_receive.restype = ctypes.c_char_p
        self.lib.td_json_client_destroy.argtypes = [ctypes.c_void_p]

        self.client_id = self.lib.td_json_client_create()
        if not self.client_id:
            raise TDLibBindingException("Failed to create TDLib client instance.")

        self._running = False
        self._receiver_thread: Optional[threading.Thread] = None
        self._futures: Dict[str, asyncio.Future] = {}
        
        # التقاط الـ Event Loop الحالي
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = asyncio.get_event_loop()

        self._update_handler: Optional[Callable[[Dict[str, Any]], None]] = None

    def start(self, update_handler: Optional[Callable[[Dict[str, Any]], None]] = None):
        """بدء حلقة الاستقبال في Thread منفصل."""
        if self._running:
            return
        self._update_handler = update_handler
        self._running = True
        self._receiver_thread = threading.Thread(target=self._receive_loop, daemon=True)
        self._receiver_thread.start()

    def _receive_loop(self):
        """حلقة لا نهائية تستقبل الأحداث من C وتوجهها للـ asyncio."""
        while self._running:
            res_ptr = self.lib.td_json_client_receive(self.client_id, 1.0)
            if res_ptr:
                res_str = res_ptr.decode('utf-8')
                try:
                    data = json.loads(res_str)
                    extra = data.get('@extra')
                    if extra and extra in self._futures:
                        future = self._futures.pop(extra)
                        if not future.done():
                            self._loop.call_soon_threadsafe(future.set_result, data)
                    elif self._update_handler:
                        self._loop.call_soon_threadsafe(self._update_handler, data)
                except Exception as e:
                    logger.error(f"[TDLibBinding] Error parsing JSON: {e}")

    async def send(self, request_data: Dict[str, Any], timeout: float = 60.0) -> Dict[str, Any]:
        """إرسال طلب وانتظار الرد (بناءً على @extra)."""
        if not self._running:
            raise TDLibBindingException("Client is not running. Call start() first.")

        extra = request_data.get('@extra', str(uuid.uuid4()))
        request_data['@extra'] = extra

        future = self._loop.create_future()
        self._futures[extra] = future

        dumped = json.dumps(request_data).encode('utf-8')
        self.lib.td_json_client_send(self.client_id, dumped)

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._futures.pop(extra, None)
            raise TimeoutError(f"TDLib request timed out after {timeout}s")

    def send_no_wait(self, request_data: Dict[str, Any]):
        """إرسال طلب دون انتظار الرد."""
        dumped = json.dumps(request_data).encode('utf-8')
        self.lib.td_json_client_send(self.client_id, dumped)

    def stop(self):
        """إيقاف العميل بأمان وتفريغ الموارد."""
        self._running = False
        if self._receiver_thread and self._receiver_thread.is_alive():
            self._receiver_thread.join(timeout=2.0)
            
        if self.client_id:
            self.lib.td_json_client_destroy(self.client_id)
            self.client_id = None

        # إلغاء جميع العقود (Futures) المعلقة
        for future in self._futures.values():
            if not future.done():
                self._loop.call_soon_threadsafe(future.set_exception, TDLibBindingException("Client destroyed before response."))
        self._futures.clear()
