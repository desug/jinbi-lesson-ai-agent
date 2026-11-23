"""Offline regression for online failure/session contract; fictional data only."""
from unittest.mock import patch
import httpx
from sqlalchemy import select
import test_d4_r2_p5_client as boundary
from app.services import ai_function_chat
from app.models.ai_memory import AiConversation
from app.models.student_subject import StudentSubject
from app.models.lesson_record import LessonRecord


class FailureContractTests(boundary.RealBoundaryTests):
    def test_other_legal_clarification_and_cancel_have_owned_session(self):
        self.output = {'intent':'clarification','parameters':{},'complete':False,
                       'ambiguity':'missing_student','action':'clarify','tool':None,
                       'reason':'missing_student','question':'请明确学员'}
        with patch.object(ai_function_chat, 'execute_tool', wraps=ai_function_chat.execute_tool) as tool:
            result = self.post('查询剩余课时')
            self.assertEqual(result['status'], 'clarification')
            self.assertTrue(self.planner.last_diagnostic['source_valid'])
            cancelled = self.post('取消')
            for item in (result, cancelled):
                with self.app.state.demo_sessions() as db:
                    self.assertEqual(db.get(AiConversation, item['conversationId']).admin_id, 1)
            self.assertEqual(tool.call_count, 0)

    def test_T2_T10_valid_missing_scope_keeps_session(self):
        self.output = boundary.MISSING
        first = self.post('查询虚构小明的剩余课时')
        self.assertEqual(first['status'], 'clarification')
        self.assertGreater(first['conversationId'], 0)
        self.assertTrue(self.planner.last_diagnostic['source_valid'])
        second = self.post('选择第二位', first['conversationId'])
        self.assertIn('科目', second['answer'])
        self.assertEqual(second['conversationId'], first['conversationId'])
        self.output = boundary.READY
        done = self.post('数学', first['conversationId'])
        self.assertEqual(done['status'], 'complete')
        self.assertEqual(done['conversationId'], first['conversationId'])

    def test_T4_T5_json_schema_independent_of_source(self):
        self.output = {**boundary.MISSING, 'parameters': {'student_name': '小明'}}
        with patch.object(ai_function_chat, 'execute_tool', wraps=ai_function_chat.execute_tool) as tool:
            result = self.post('查询虚构小明的剩余课时')
            d = self.planner.last_diagnostic
            self.assertTrue(d['json_valid']); self.assertTrue(d['schema_valid'])
            self.assertFalse(d['source_valid'])
            self.assertEqual(d['source_failure'], 'parameter_mismatch')
            self.assertEqual(result['errorCode'], 'model_source_invalid')
            self.assertEqual(result['recovery'], 'new_session')
            self.assertNotIn('conversationId', result)
            self.assertEqual(tool.call_count, 0)
        with self.app.state.demo_sessions() as db:
            self.assertEqual(list(db.scalars(select(AiConversation))), [])

    def test_T6_T7_T8_failure_contract_and_retry(self):
        with patch.object(ai_function_chat, 'execute_tool', wraps=ai_function_chat.execute_tool) as tool:
            self.failure = httpx.ReadTimeout('synthetic-only')
            self.assertEqual(self.post('查询虚构小明数学剩余课时')['errorCode'], 'model_timeout')
            self.failure = None
            self.output = '{bad'
            self.assertEqual(self.post('查询虚构小明数学剩余课时')['errorCode'], 'model_invalid_json')
            self.output = {**boundary.READY, 'tool': 'delete_student'}
            self.assertEqual(self.post('查询虚构小明数学剩余课时')['errorCode'], 'model_invalid_schema')
            self.assertEqual(tool.call_count, 0)
            self.output = boundary.READY
            first = self.post('查询虚构小明数学剩余课时')
            done = self.post('选择第二位', first['conversationId'])
            self.assertEqual(done['status'], 'complete'); self.assertEqual(tool.call_count, 1)

    def test_existing_session_failure_clear_context_then_retry(self):
        first = self.post('查询虚构小明数学剩余课时')
        self.output = {**boundary.READY, 'parameters': {'student_name': '虚构小明', 'subject': '英语'}}
        bad = self.post('查询虚构小明数学剩余课时', first['conversationId'])
        self.assertEqual(bad['errorCode'], 'model_source_invalid')
        self.assertEqual(bad['conversationId'], first['conversationId'])
        self.assertEqual(bad['recovery'], 'retry_current')
        with self.app.state.demo_sessions() as db:
            self.assertEqual(db.get(AiConversation, first['conversationId']).context, {})
        self.output = boundary.READY
        self.assertEqual(self.post('查询虚构小明数学剩余课时', first['conversationId'])['status'], 'clarification')

    def test_T9_T12_write_snapshot_and_admin_ownership(self):
        def snapshot():
            with self.app.state.demo_sessions() as db:
                return [[tuple(getattr(x,c.key) for c in model.__mapper__.column_attrs) for x in db.scalars(select(model).order_by(model.id))] for model in (StudentSubject, LessonRecord)]
        before = snapshot()
        result = self.post('帮虚构小明增加三节数学课。')
        self.assertIn('不支持修改', result['answer']); self.assertEqual(len(self.requests), 0)
        self.assertEqual(before, snapshot())
        other = self.http.post('/auth/admin-login', json={'username':'fictional-demo-2','password':'fictional-demo-only'})
        headers = {'Authorization':'Bearer '+other.json()['token']}
        denied = self.http.post('/admin/ai/chat', headers=headers, json={'query':'数学','conversationId':result['conversationId']})
        self.assertEqual(denied.status_code, 404)
        new = self.http.post('/admin/ai/chat', headers=headers, json={'query':'查询虚构小明数学剩余课时'}).json()
        self.assertNotEqual(new['conversationId'], result['conversationId'])
        with self.app.state.demo_sessions() as db:
            self.assertEqual(db.get(AiConversation,new['conversationId']).admin_id, 2)
