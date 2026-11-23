import json
import unittest
from types import SimpleNamespace as NS
from test_ai_query_safety import QuerySafetyTests
from test_function_chat import Client, call
from app.services.ai_function_chat import run_chat


class SafePlanningTests(QuerySafetyTests):
    def test_multi_call_and_final_tool_result_is_minimal(self):
        client = Client([[call('get_student_hours', '{"student_ref":"REF"}', 'c1')],
                         [call('get_student_hours', '{"student_ref":"REF"}', 'c2')], []])
        result = run_chat(self.db, '请查小王电话13800000001的数学课时，备注家庭信息', client)
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(len(result['trace']), 2)
        sent = json.dumps(client.requests, ensure_ascii=False)
        for secret in ('小王', '13800000001', 'S1', '家庭信息', 'remainingHours', 'studentId'):
            self.assertNotIn(secret, sent)
        self.assertIn('tool_call_id', sent)
        self.assertIn('executed', sent)
        self.assertEqual(client.requests[-1]['tool_choice'], 'none')

    def test_tool_limit_never_executes_extra_calls(self):
        calls = [call('get_student_hours', '{"student_ref":"REF"}', 'c' + str(i)) for i in range(5)]
        result = run_chat(self.db, '查询S1数学课时', Client([calls]))
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['data'], [])

    def test_wrong_reference_and_extra_arguments(self):
        for args in ('{"student_ref":"other"}', '{"student_ref":"REF","sql":"DROP"}'):
            self.assertEqual(run_chat(self.db, '查询S1数学课时', Client([[call('get_student_hours', args)] ]))['status'], 'error')

    def test_timeout_and_bad_finish_reason(self):
        client = Client([])
        client.create = lambda **kwargs: (_ for _ in ()).throw(TimeoutError('secret 13800000001'))
        result = run_chat(self.db, '查询S1数学课时', client)
        self.assertEqual(result['status'], 'error')
        self.assertNotIn('13800000001', str(result))
        client.create = lambda **kwargs: NS(choices=[NS(finish_reason='length', message=NS(tool_calls=[]))])
        self.assertEqual(run_chat(self.db, '查询S1数学课时', client)['status'], 'error')

    def test_local_ambiguity_and_missing_identity(self):
        client = Client([])
        result = run_chat(self.db, '查小王数学课时', client)
        self.assertEqual(len(result['candidates']), 2)
        self.assertEqual(client.requests, [])
        self.assertEqual(run_chat(self.db, '英语课时', client)['status'], 'clarification')

    def test_refs_change_between_requests(self):
        c1 = Client([[call('get_student_hours', '{"student_ref":"REF"}')], []])
        c2 = Client([[call('get_student_hours', '{"student_ref":"REF"}')], []])
        run_chat(self.db, '查S1数学课时', c1)
        run_chat(self.db, '查S1数学课时', c2)
        ref1 = json.loads(c1.requests[0]['messages'][1]['content'])['student_ref']
        ref2 = json.loads(c2.requests[0]['messages'][1]['content'])['student_ref']
        self.assertNotEqual(ref1, ref2)
