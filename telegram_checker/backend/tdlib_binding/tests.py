import unittest
import asyncio
import json
from unittest.mock import MagicMock, patch
from telegram_checker.backend.tdlib_binding.core import TDLibClient, TDLibBindingException

class TestTDLibBinding(unittest.IsolatedAsyncioTestCase):
    
    @patch('telegram_checker.backend.tdlib_binding.core.ctypes.CDLL')
    def setUp(self, mock_cdll):
        # محاكاة (Mock) لمكتبة libtdjson
        self.mock_lib = MagicMock()
        mock_cdll.return_value = self.mock_lib
        self.mock_lib.td_json_client_create.return_value = 12345
        
        self.client = TDLibClient(lib_path="dummy.so")
        self.loop = asyncio.get_event_loop()

    def test_client_creation(self):
        """التأكد من إنشاء العميل واستدعاء الدالة الصحيحة من C"""
        self.assertEqual(self.client.client_id, 12345)
        self.client.lib.td_json_client_create.assert_called_once()

    @patch('telegram_checker.backend.tdlib_binding.core.ctypes.CDLL')
    def test_client_creation_failure(self, mock_cdll):
        """التأكد من رفع استثناء إذا فشل تحميل المكتبة"""
        mock_cdll.side_effect = OSError("File not found")
        with self.assertRaises(TDLibBindingException):
            TDLibClient(lib_path="invalid.so")

    async def test_send_and_receive(self):
        """التأكد من صحة إرسال طلب واستقبال استجابة وتطابق الـ Request ID"""
        self.client.start()
        
        # تجهيز استجابة وهمية من مكتبة C
        request = {"@type": "getAuthorizationState", "@extra": "test_id_1"}
        response = {"@type": "authorizationStateWaitTdlibParameters", "@extra": "test_id_1"}
        
        # محاكاة الاستقبال بحيث تُرجع الاستجابة في المرة الأولى ثم None لاحقاً
        def mock_receive(client_id, timeout):
            if not hasattr(self, '_called_once'):
                self._called_once = True
                return json.dumps(response).encode('utf-8')
            return None
            
        self.client.lib.td_json_client_receive.side_effect = mock_receive
        
        # إرسال الطلب
        result = await self.client.send(request, timeout=2.0)
        
        # التحقق من الإرسال الفعلي لمكتبة C
        self.client.lib.td_json_client_send.assert_called_with(
            12345, json.dumps(request).encode('utf-8')
        )
        
        # التحقق من الاستجابة المُحللة
        self.assertEqual(result["@type"], "authorizationStateWaitTdlibParameters")
        
        self.client.stop()

    async def test_timeout_handling(self):
        """التأكد من إدارة الأخطاء عند تأخر الرد (Timeout)"""
        self.client.start()
        
        # محاكاة الصمت من C (لا توجد استجابة)
        self.client.lib.td_json_client_receive.return_value = None
        
        with self.assertRaises(TimeoutError):
            await self.client.send({"@type": "test"}, timeout=0.1)
            
        self.client.stop()

    def test_stop_client(self):
        """التأكد من إغلاق العميل وتحرير الموارد"""
        self.client.start()
        self.assertTrue(self.client._running)
        self.client.stop()
        self.assertFalse(self.client._running)
        self.client.lib.td_json_client_destroy.assert_called_with(12345)

if __name__ == "__main__":
    unittest.main()
