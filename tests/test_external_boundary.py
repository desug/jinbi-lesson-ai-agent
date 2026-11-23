import unittest
from unittest.mock import patch
from test_ai_query_safety import QuerySafetyTests
from app.services.ai_function_chat import run_chat
from app.services.ai_query_service import answer_ai_query
from app.schemas.auth import TokenPayload

class ExternalBoundaryTests(QuerySafetyTests):
    def test_live_model_disabled_even_when_key_configured(self):
        with patch('app.services.ai_function_chat.settings.deepseek_api_key', 'dummy-key'):
            result = run_chat(self.db, '张三 13800000001 学号 S1')
        self.assertEqual(result['status'], 'degraded')
        self.assertNotIn('13800000001', str(result))

    def test_legacy_endpoint_never_invokes_remote_parser(self):
        with patch('app.services.ai_query_service._parse_query_with_deepseek', side_effect=AssertionError('external call')):
            result = answer_ai_query(self.db, TokenPayload(sub='1', role='admin'), '一个无法识别的问题')
        self.assertTrue(result.answer)
