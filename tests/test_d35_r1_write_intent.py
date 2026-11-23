"""Write-intent regression using isolated fictional SQLite and real demo HTTP auth."""
import unittest
from contextlib import ExitStack, contextmanager
from unittest.mock import patch
from sqlalchemy import select
from fastapi.testclient import TestClient
from app.models.ai_memory import AiConversation
from app.models.lesson_record import LessonRecord
from app.models.student_subject import StudentSubject
from app.offline_demo_app import create_demo_app
from app.services import ai_conversation_chat as conversation, ai_function_chat as function, ai_tools
from app.services.offline_demo_planner import OfflineDemoPlanner

COMMANDS = (
    '帮虚构小明增加三节数学课。',
    '给虚构小明加三节数学课。',
    '给虚构小明加上三节数学课。',
    '帮虚构小明扣除两节课。',
    '将虚构小明的数学课时减少三节。',
    '修改虚构小明的剩余课时。',
)

@contextmanager
def reject_if_dispatched():
    with ExitStack() as stack:
        for module, name in ((conversation,'run_chat'),(function,'resolve_student'),
                             (function,'execute_tool'),(ai_tools,'execute_tool'),
                             (OfflineDemoPlanner,'create')):
            stack.enter_context(patch.object(module,name,side_effect=AssertionError(f'{name} must not run')))
        yield

class WriteIntentTests(unittest.TestCase):
    def setUp(self):
        self.app=create_demo_app(); self.client=TestClient(self.app);self.client.__enter__()
        def login(name):
            r=self.client.post('/auth/admin-login',json={'username':name,'password':'fictional-demo-only'});self.assertEqual(r.status_code,200);return {'Authorization':'Bearer '+r.json()['token']}
        self.admin=login('fictional-demo'); self.other=login('fictional-demo-2')
        with self.app.state.demo_sessions() as db:
            db.add(LessonRecord(student_id=2,subject_name='数学',change_type='deduct',hours=2,amount=0,remaining_hours=10,operator_name='fictional-operator',remark='虚构流水'))
            db.commit()
    def tearDown(self): self.client.__exit__(None,None,None)
    def post(self,q,cid=None,selected=None,headers=None):
        body={'query':q}
        if cid is not None: body['conversationId']=cid
        if selected is not None: body['selectedStudentId']=selected
        return self.client.post('/admin/ai/chat',headers=headers or self.admin,json=body)
    def snapshot(self):
        with self.app.state.demo_sessions() as db:
            def rows(model):
                return [tuple(getattr(row,column.key) for column in model.__mapper__.column_attrs) for row in db.scalars(select(model).order_by(model.id))]
            return rows(StudentSubject),rows(LessonRecord)
    def context(self,cid):
        with self.app.state.demo_sessions() as db: return dict(db.get(AiConversation,cid).context or {})
    def assert_rejected(self,r):
        self.assertEqual(r.status_code,200);j=r.json();self.assertEqual(j['status'],'clarification')
        self.assertIn('不支持修改课时',j['answer']);self.assertEqual(j['candidates'],[]);self.assertEqual(j['data'],[]);self.assertEqual(j['trace'],[])
        self.assertEqual(self.context(j['conversationId']),{})
        return j
    def pending(self):
        r=self.post('查询虚构小明剩余课时');self.assertEqual(r.status_code,200);j=r.json();self.assertEqual(len(j['candidates']),2);return j
    def test_http_commands_reject_before_candidate_or_tool(self):
        before=self.snapshot()
        with reject_if_dispatched():
            for q in COMMANDS:
                with self.subTest(query=q): self.assert_rejected(self.post(q))
        self.assertEqual(before,self.snapshot())
    def test_service_entry_rejects_before_planner(self):
        before=self.snapshot()
        with reject_if_dispatched(), self.app.state.demo_sessions() as db:
            for q in COMMANDS:
                with self.subTest(query=q):
                    j=conversation.chat(db,1,q,client=OfflineDemoPlanner(),demo_scope_followup=True)
                    self.assertIn('不支持修改课时',j['answer']);self.assertEqual(j['candidates'],[]);self.assertEqual(j['data'],[])
        self.assertEqual(before,self.snapshot())
    def test_write_interrupts_pending_candidate_and_clears_identity(self):
        before=self.snapshot()
        for with_selection in (False,True):
            state=self.pending();cid=state['conversationId'];chosen=state['candidates'][1]['studentId']
            with reject_if_dispatched():
                self.assert_rejected(self.post(COMMANDS[0],cid,chosen if with_selection else None))
            self.assertEqual(self.post('选择第二位',cid,chosen).status_code,422)
            self.assertEqual(self.post('数学',cid).json()['data'],[])
        self.assertEqual(before,self.snapshot())
    def test_write_interrupts_pending_subject_and_clears_identity(self):
        before=self.snapshot();state=self.pending();cid=state['conversationId']
        selected=self.post('选择第二位',cid).json();self.assertIn('科目',selected['answer']);self.assertIn('demo_pending_scope',self.context(cid))
        with reject_if_dispatched(): self.assert_rejected(self.post(COMMANDS[0],cid))
        self.assertEqual(self.post('数学',cid).json()['data'],[]);self.assertEqual(before,self.snapshot())
    def test_read_only_rule_and_ambiguous_questions(self):
        before=self.snapshot();r=self.post('虚构小明数学还剩多少课时？');j=r.json();self.assertEqual(len(j['candidates']),2)
        r=self.post('选择第二位',j['conversationId']);self.assertEqual(r.json()['data'][0]['result']['subjects'][0]['remainingHours'],10)
        original=ai_tools.execute_tool
        with patch.object(ai_tools,'execute_tool',wraps=original) as search:
            for q in ('增加课时需要什么审批规则？','修改虚构小明的剩余课时需要什么审批规则？'):
                with self.subTest(query=q):
                    r=self.post(q);self.assertEqual(r.status_code,200);self.assertEqual(r.json()['status'],'no_approved_rules');self.assertEqual(r.json()['citations'],[])
            self.assertEqual([c.args[1] for c in search.call_args_list],['search_demo_academic_rules','search_demo_academic_rules'])
        with patch.object(function,'execute_tool',side_effect=AssertionError('tool must not run')):
            r=self.post('虚构小明加课的事情怎么办？');self.assertEqual(r.status_code,200);j=r.json();self.assertEqual(j['status'],'clarification');self.assertFalse(j['data'])
        self.assertEqual(before,self.snapshot())
    def test_ownership_and_forged_student_id_still_rejected(self):
        self.assertEqual(self.client.post('/admin/ai/chat',json={'query':COMMANDS[0]}).status_code,401)
        self.assertEqual(self.post(COMMANDS[0],selected=999).status_code,422)
        state=self.pending();cid=state['conversationId'];before=self.context(cid)
        with reject_if_dispatched():
            self.assertEqual(self.post(COMMANDS[0],cid,999).status_code,422)
            self.assertEqual(self.post(COMMANDS[0],cid,headers=self.other).status_code,404)
        self.assertEqual(before,self.context(cid))
