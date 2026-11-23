from types import SimpleNamespace as NS
import json
from fastapi import HTTPException
from test_ai_query_safety import QuerySafetyTests
from app.services.ai_function_chat import run_chat
from app.utils.deps import get_current_admin
from app.schemas.auth import TokenPayload


def call(name, arguments, call_id='call_1'):
    return NS(id=call_id, function=NS(name=name, arguments=arguments))


class Client:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []
        self.chat = NS(completions=self)
    def create(self, **kwargs):
        self.requests.append(kwargs)
        ref = json.loads(kwargs['messages'][1]['content'])['student_ref']
        calls = next(self.responses)
        calls = [call(c.function.name, c.function.arguments.replace('REF', ref), c.id) for c in calls]
        return NS(choices=[NS(message=NS(tool_calls=calls), finish_reason='tool_calls' if calls else 'stop')])


class FunctionChatTests(QuerySafetyTests):
    def test_model_selects_tools_and_passes_parameters(self):
        client = Client([[call('get_student_hours', '{"student_ref":"REF"}')], []])
        result = run_chat(self.db, '查询S1数学课时', client)
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['data'][0]['result']['subjects'][0]['remainingHours'], -1)
        sent = json.dumps(client.requests, ensure_ascii=False)
        for secret in ('小王', 'S1', '13800000001', 'remainingHours'):
            self.assertNotIn(secret, sent)

    def test_unverified_id_and_sql_rejected(self):
        for name, args in [('get_student_hours', '{"student_id":1}'), ('execute_sql', '{"sql":"DELETE"}')]:
            result = run_chat(self.db, '查询S1数学课时', Client([[call(name, args)]]))
            self.assertEqual(result['status'], 'error')

    def test_student_cannot_access_admin_dependency(self):
        with self.assertRaises(HTTPException) as error:
            get_current_admin(TokenPayload(sub='1', role='student'))
        self.assertEqual(error.exception.status_code, 403)
