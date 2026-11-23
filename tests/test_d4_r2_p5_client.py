import json
import io
import logging
from contextlib import redirect_stdout, redirect_stderr
import unittest
from unittest.mock import patch
import httpx
from fastapi.testclient import TestClient
from app.offline_demo_app import create_demo_app
from app.services.demo_deepseek_client import NaturalDeepSeekPlanner, RestrictedTransport
from app.services.offline_demo_planner import OfflineDemoPlanner
from app.services import ai_function_chat
from test_d4_r2_p3_mock_online import READY, MISSING

class RealBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.requests=[]; self.output=READY; self.status=200; self.finish='stop'; self.failure=None
        def handler(req):
            self.requests.append(req)
            if self.failure: raise self.failure
            if self.status != 200: return httpx.Response(self.status,request=req,headers={'location':'https://other.invalid/'})
            return httpx.Response(200,request=req,json={'id':'mock','object':'chat.completion','created':1,'model':'mock','choices':[{'index':0,'finish_reason':self.finish,'message':{'role':'assistant','content':self.output if isinstance(self.output,str) else json.dumps(self.output,ensure_ascii=False)}}]})
        self.planner=NaturalDeepSeekPlanner(api_key='synthetic-key-only',test_transport=httpx.MockTransport(handler)); self.addCleanup(self.planner.close)
        self.app=create_demo_app(mode='deepseek_prepared',trusted_planner=self.planner)
        self.http=TestClient(self.app);self.http.__enter__();self.addCleanup(self.http.__exit__,None,None,None)
        r=self.http.post('/auth/admin-login',json={'username':'fictional-demo','password':'fictional-demo-only'});self.auth={'Authorization':'Bearer '+r.json()['token']}
    def post(self,q,cid=None):
        r=self.http.post('/admin/ai/chat',headers=self.auth,json={'query':q,**({'conversationId':cid} if cid else {})});self.assertEqual(r.status_code,200);return r.json()
    def test_page_does_not_claim_connection(self):
        page=self.http.get('/demo').text
        self.assertIn('连接状态未验证',page); self.assertNotIn('确定性离线演示',page); self.assertNotIn('真实 DeepSeek 已连接',page)
    def test_http_suggestion_to_tool_and_scope(self):
        with patch.object(OfflineDemoPlanner,'__init__',side_effect=AssertionError('offline forbidden')), patch.object(ai_function_chat,'execute_tool',wraps=ai_function_chat.execute_tool) as tool:
            q='帮我看看虚构小明数学课还有没有剩余。'; a=self.post(q); self.assertEqual(len(a['candidates']),2)
            b=self.post('第二位。',a['conversationId']); self.assertEqual(b['data'][0]['result']['subjects'][0]['remainingHours'],10);self.assertEqual(tool.call_count,1)
            req=self.requests[0]; self.assertEqual(str(req.url),'https://api.deepseek.com/chat/completions');body=json.loads(req.content);self.assertEqual(body['messages'][1]['content'],q); self.assertEqual(body['response_format'],{'type':'json_object'});self.assertNotIn('synthetic-key-only',req.content.decode())
            self.output=MISSING;a=self.post('查一下虚构小明还剩多少课。');self.post('第二位。',a['conversationId']);self.output=READY;b=self.post('数学',a['conversationId']);self.assertEqual(b['data'][0]['result']['subjects'][0]['remainingHours'],10);self.assertEqual(tool.call_count,2)
    def test_failures_no_tool_or_fallback(self):
        with patch.object(OfflineDemoPlanner,'__init__',side_effect=AssertionError('offline forbidden')),patch.object(ai_function_chat,'execute_tool',wraps=ai_function_chat.execute_tool) as tool:
            q='帮我看看虚构小明数学课还有没有剩余。'
            for status in (401,403,429,302):
                self.status=status;self.assertFalse(self.post(q)['data'])
            self.status=200
            for value in ('',' ','{bad',json.dumps({**READY,'parameters':{'student_id':'1'}}),json.dumps({**READY,'tool':'delete'}),json.dumps({**READY,'parameters':{'student_name':'虚构小明','subject':'英语'}},ensure_ascii=False),'x'*70000):
                self.output=value;self.assertFalse(self.post(q)['data'])
            self.output=READY;self.finish='length';self.assertFalse(self.post(q)['data']);self.finish='stop';self.failure=httpx.ReadTimeout('synthetic');self.assertFalse(self.post(q)['data'])
            self.assertEqual(tool.call_count,0)
    def test_write_and_sensitive_never_sent(self):
        with patch.object(ai_function_chat,'execute_tool',wraps=ai_function_chat.execute_tool) as tool:
            self.assertIn('不支持修改',self.post('帮虚构小明增加三节数学课。')['answer'])
            self.assertFalse(self.post('虚构小明数学 sk-synthetic-sensitive')['data']);self.assertEqual(len(self.requests),0);self.assertEqual(tool.call_count,0)
    def test_synthetic_secrets_not_in_logs_or_responses(self):
        capture=io.StringIO(); root=logging.getLogger(); handler=logging.StreamHandler(capture); old=root.level
        root.addHandler(handler); root.setLevel(logging.DEBUG)
        try:
            with redirect_stdout(capture), redirect_stderr(capture):
                good=self.post('帮我看看虚构小明数学课还有没有剩余。')
                bad=self.post('虚构小明数学 sk-synthetic-sensitive')
                self.status=403; failed=self.post('帮我看看虚构小明数学课还有没有剩余。')
        finally:
            root.removeHandler(handler); root.setLevel(old)
        for value in ('synthetic-key-only','sk-synthetic-sensitive'):
            self.assertNotIn(value,capture.getvalue()); self.assertNotIn(value,json.dumps([good,bad,failed],ensure_ascii=False))
            for req in self.requests: self.assertNotIn(value,req.content.decode())

    def test_transport_blocks_other_destinations(self):
        transport=RestrictedTransport(httpx.MockTransport(lambda req: self.fail('must not dispatch')))
        with self.assertRaises(httpx.RequestError): transport.handle_request(httpx.Request('POST','https://other.invalid/chat/completions'))
        encoded=RestrictedTransport(httpx.MockTransport(lambda req: httpx.Response(200,headers={'content-encoding':'gzip'},content=b'x',request=req)))
        with self.assertRaises(httpx.RequestError): encoded.handle_request(httpx.Request('POST','https://api.deepseek.com/chat/completions'))
        with self.assertRaises(ValueError):create_demo_app(mode='deepseek_prepared')
        with self.assertRaises(ValueError):NaturalDeepSeekPlanner(api_key='synthetic-key-only',test_transport=httpx.HTTPTransport())
