import json
import io
import logging
from contextlib import redirect_stdout, redirect_stderr
import unittest
from unittest.mock import patch
import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.offline_demo_app import create_demo_app
from app.models.student_subject import StudentSubject
from app.models.lesson_record import LessonRecord
from app.models.ai_memory import AiMessage, AiConversation
from app.services import ai_function_chat

READY={'intent':'student_hours','parameters':{'student_name':'虚构小明','subject':'数学'},'complete':True,'ambiguity':'none','action':'propose_tool','tool':'get_student_hours','reason':'ready','question':''}
MISSING={'intent':'student_hours','parameters':{'student_name':'虚构小明'},'complete':False,'ambiguity':'missing_scope','action':'clarify','tool':None,'reason':'missing_scope','question':'请明确科目'}

class MockOnlineTests(unittest.TestCase):
    def setUp(self):
        self.requests=[]; self.output=READY; self.failure=None
        def handler(req):
            self.requests.append(json.loads(req.content))
            if self.failure: raise self.failure
            return httpx.Response(200,json={'id':'mock','object':'chat.completion','created':1,'model':'mock-only','choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':self.output if isinstance(self.output,str) else json.dumps(self.output,ensure_ascii=False)}}]},request=req)
        self.app=create_demo_app(mode='deepseek_online',mock_transport=httpx.MockTransport(handler))
        self.http=TestClient(self.app); self.http.__enter__(); self.addCleanup(self.http.__exit__,None,None,None)
        login=self.http.post('/auth/admin-login',json={'username':'fictional-demo','password':'fictional-demo-only'})
        self.auth={'Authorization':'Bearer '+login.json()['token']}
    def post(self,q,cid=None,selected=None):
        body={'query':q}
        if cid is not None: body['conversationId']=cid
        if selected is not None: body['selectedStudentId']=selected
        r=self.http.post('/admin/ai/chat',headers=self.auth,json=body)
        self.assertEqual(r.status_code,200); return r.json()
    def snapshot(self):
        with self.app.state.demo_sessions() as db:
            return [[tuple(getattr(x,c.key) for c in model.__mapper__.column_attrs) for x in db.scalars(select(model).order_by(model.id))] for model in (StudentSubject,LessonRecord)]
    def test_natural_query_confirmation_and_real_sqlite_read(self):
        q='帮我看看虚构小明数学课还有没有剩余。'
        with patch.object(ai_function_chat,'execute_tool',wraps=ai_function_chat.execute_tool) as tool:
            first=self.post(q); self.assertEqual(len(first['candidates']),2); self.assertEqual(tool.call_count,0)
            body=self.requests[0]; self.assertEqual(body['messages'][1]['content'],q); self.assertEqual(len(body['messages']),2)
            self.assertNotIn('studentId',json.dumps(body)); self.assertNotIn(self.auth['Authorization'],json.dumps(body))
            done=self.post('选择第二位',first['conversationId'])
            self.assertEqual(done['data'][0]['result']['subjects'][0]['remainingHours'],10)
            self.assertEqual(tool.call_count,1); self.assertEqual(len(self.requests),1)
    def test_missing_scope_followup(self):
        self.output=MISSING
        with patch.object(ai_function_chat,'execute_tool',wraps=ai_function_chat.execute_tool) as tool:
            first=self.post('查一下虚构小明还剩多少课。')
            second=self.post('选择第二位',first['conversationId']); self.assertIn('科目',second['answer']); self.assertEqual(tool.call_count,0)
            self.output={**READY, 'parameters': {'student_name': '虚构小红', 'subject': '数学'}}
            third=self.post('数学',first['conversationId']); self.assertEqual(third['data'][0]['result']['subjects'][0]['remainingHours'],10); self.assertEqual(tool.call_count,1); self.assertEqual(len(self.requests),1)
    def test_write_is_blocked_before_model_and_tool(self):
        before=self.snapshot()
        with patch.object(ai_function_chat,'execute_tool',wraps=ai_function_chat.execute_tool) as tool:
            result=self.post('帮虚构小明增加三节数学课。'); self.assertIn('不支持修改课时',result['answer']); self.assertEqual(tool.call_count,0)
        self.assertEqual(len(self.requests),0); self.assertEqual(before,self.snapshot())
    def test_invalid_suggestions_fail_closed(self):
        before=self.snapshot()
        invalid=[{**READY,'parameters':{'student_id':'1','subject':'数学'}}, {**READY,'tool':'search_demo_academic_rules'}, {}, '', {**READY,'parameters':{'student_name':'虚构小明','subject':'英语'}}]
        with patch.object(ai_function_chat,'execute_tool',wraps=ai_function_chat.execute_tool) as tool:
            for value in invalid:
                self.output=value; self.assertEqual(self.post('帮我看看虚构小明数学课还有没有剩余。')['status'],'error')
            self.failure=httpx.ReadTimeout('synthetic'); self.assertEqual(self.post('帮我看看虚构小明数学课还有没有剩余。')['status'],'error')
            self.assertEqual(tool.call_count,0)
        self.assertEqual(len(self.requests),6); self.assertEqual(before,self.snapshot())
    def test_sensitive_input_never_sent_or_stored(self):
        marker='sk-synthetic-sensitive-marker'
        capture=io.StringIO(); handler=logging.StreamHandler(capture); root=logging.getLogger(); root.addHandler(handler)
        try:
            with redirect_stdout(capture), redirect_stderr(capture), patch.object(ai_function_chat,'execute_tool',wraps=ai_function_chat.execute_tool) as tool:
                self.assertEqual(self.post('虚构小明数学 '+marker)['status'],'error')
                self.assertIn('不支持修改课时',self.post('帮虚构小明增加三节数学课。'+marker)['answer'])
                self.assertEqual(tool.call_count,0)
        finally:
            root.removeHandler(handler)
        self.assertNotIn(marker,capture.getvalue())
        self.assertEqual(self.requests,[])
        with self.app.state.demo_sessions() as db:
            self.assertFalse(any(marker in x.content for x in db.scalars(select(AiMessage))))
            self.assertFalse(any(marker in x.title for x in db.scalars(select(AiConversation))))
    def test_factory_fails_closed(self):
        for value in (None,httpx.HTTPTransport()):
            with self.assertRaises(ValueError): create_demo_app(mode='deepseek_online',mock_transport=value)
        with self.assertRaises(ValueError): create_demo_app(mode='offline_deterministic',mock_transport=httpx.MockTransport(lambda r: None))
