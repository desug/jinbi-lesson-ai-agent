import contextlib
import io
import json
from unittest.mock import patch
from test_ai_query_safety import QuerySafetyTests
from test_function_chat import Client, call
from app.services.ai_function_chat import run_chat
from app.services.ai_query_service import answer_ai_query
from app.schemas.auth import TokenPayload


class PrivacyAuditTests(QuerySafetyTests):
    def test_complete_request_is_allowlisted(self):
        client = Client([[call('get_student_hours', '{"student_ref":"REF"}')], []])
        out = run_chat(self.db, '查询手机号13800000001小王数学课时，学号S1，缴费123元', client)
        self.assertEqual(out['status'], 'complete')
        payload = json.dumps(client.requests, ensure_ascii=False)
        for secret in ('小王', '13800000001', 'S1', '缴费', '123元', 'remainingHours', 'studentId'):
            self.assertNotIn(secret, payload)
        message = json.loads(client.requests[0]['messages'][1]['content'])
        self.assertEqual(set(message), {'student_ref', 'subject', 'task'})
        self.assertEqual(client.requests[1]['messages'][-1]['content'], '{"executed":true}')

    def test_model_errors_never_echo_secrets(self):
        for error in (TimeoutError('key-secret'), RuntimeError('429 phone-secret'), RuntimeError('service-secret')):
            client = Client([])
            def fail(**kwargs): raise error
            client.create = fail
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(stderr):
                result = run_chat(self.db, '查询S1数学课时', client)
            self.assertEqual(result['status'], 'error')
            self.assertNotIn('secret', str(result) + stderr.getvalue())

    def test_invalid_json_and_duplicate_keys(self):
        for arguments in ('{', '{"student_ref":"REF","student_ref":"REF"}'):
            result = run_chat(self.db, '查S1数学课时', Client([[call('get_student_hours', arguments)]]))
            self.assertEqual(result['status'], 'error')
            self.assertEqual(result['data'], [])

    def test_duplicate_tool_id_rejected(self):
        c = Client([[call('get_student_hours', '{"student_ref":"REF"}', 'same'), call('get_student_hours', '{"student_ref":"REF"}', 'same')]])
        self.assertEqual(run_chat(self.db, '查S1数学课时', c)['data'], [])

    def test_database_exception_does_not_leak(self):
        with patch('app.services.ai_query_service._parse_query_with_rules', side_effect=RuntimeError('SENSITIVE-PASSWORD')):
            with self.assertRaises(Exception) as e:
                answer_ai_query(self.db, TokenPayload(sub='1', role='admin'), '敏感问题')
        self.assertNotIn('SENSITIVE-PASSWORD', str(e.exception))
        self.assertNotIn('SENSITIVE-PASSWORD', str(e.exception.detail))
