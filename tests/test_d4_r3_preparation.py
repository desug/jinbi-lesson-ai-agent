import io
import json
import logging
import threading
import tempfile
import ssl
import sqlite3
from contextlib import closing
from pathlib import Path
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import httpx
from app.services.demo_deepseek_client import NaturalDeepSeekPlanner, RestrictedTransport, DurableBudget, MAX_MODEL_REQUESTS
from test_d4_r2_p3_mock_online import READY

Q='帮我看看虚构小明数学课还有没有剩余。'

class PreparationTests(unittest.TestCase):
    def test_request_limit_enforced_under_concurrency_and_failures(self):
        calls=[]; guard=threading.Lock()
        def handler(request):
            with guard: calls.append(request.url.path)
            raise httpx.ReadTimeout('synthetic-unsafe-marker',request=request)
        transport=RestrictedTransport(httpx.MockTransport(handler))
        def send(_):
            try: transport.handle_request(httpx.Request('POST','https://api.deepseek.com/chat/completions'))
            except httpx.RequestError: return
            self.fail('request unexpectedly succeeded')
        with ThreadPoolExecutor(max_workers=12) as pool: list(pool.map(send,range(MAX_MODEL_REQUESTS+10)))
        self.assertEqual(len(calls),100); self.assertEqual(transport.count,100)
        transport.close()

    def test_durable_budget_survives_restart_and_concurrency(self):
        root=Path(__file__).resolve().parents[1] / 'evidence'
        with tempfile.TemporaryDirectory(dir=root) as tmp:
            path=Path(tmp)/'budget.sqlite3'
            def claim(_): return DurableBudget(path).consume()
            with ThreadPoolExecutor(max_workers=12) as pool: results=list(pool.map(claim,range(MAX_MODEL_REQUESTS+10)))
            self.assertEqual(sorted(x for x in results if x is not None),list(range(1,101)))
            self.assertIsNone(DurableBudget(path).consume())
            sent=[]
            t=RestrictedTransport(httpx.MockTransport(lambda request:sent.append(request)),budget=DurableBudget(path))
            with self.assertRaises(httpx.RequestError): t.handle_request(httpx.Request('POST','https://api.deepseek.com/chat/completions'))
            self.assertEqual(sent,[])
            t.close()

    def test_legacy_usage_is_preserved_and_request_101_never_reaches_transport(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'budget.sqlite3'
            with closing(sqlite3.connect(path)) as db:
                db.execute('CREATE TABLE budget (id INTEGER PRIMARY KEY CHECK(id=1), used INTEGER NOT NULL CHECK(used BETWEEN 0 AND 10))')
                db.execute('INSERT INTO budget VALUES (1, 7)')
                db.commit()
            budget = DurableBudget(path)
            self.assertEqual(budget.status(), (7, 100))
            self.assertEqual(DurableBudget(path).status(), (7, 100))
            calls = []

            def send(request):
                calls.append(request)
                return httpx.Response(200, json={}, request=request)

            request = httpx.Request('POST', 'https://api.deepseek.com/chat/completions')
            first = RestrictedTransport(httpx.MockTransport(send), budget=budget)
            self.addCleanup(first.close)
            for count in range(8, 100):
                with closing(first.handle_request(request)) as response:
                    self.assertEqual(response.status_code, 200)
                self.assertEqual(first.count, count)
            restarted = RestrictedTransport(httpx.MockTransport(send), budget=DurableBudget(path))
            self.addCleanup(restarted.close)
            with closing(restarted.handle_request(request)) as response:
                self.assertEqual(response.status_code, 200)
            self.assertEqual(restarted.count, 100)
            fresh = RestrictedTransport(httpx.MockTransport(send), budget=DurableBudget(path))
            self.addCleanup(fresh.close)
            for transport in (restarted, fresh):
                with self.assertRaisesRegex(httpx.RequestError, 'request limit reached'):
                    transport.handle_request(request)
            self.assertEqual(len(calls), 93)
            self.assertEqual(budget.status(), (100, 100))

    @patch('app.services.demo_deepseek_client.MAX_MODEL_REQUESTS', 5)
    def test_diagnostics_are_bounded_and_secret_free(self):
        requests=[]; scenario={'n':0}; key='synthetic-key-marker'; marker='synthetic-unsafe-marker'
        def handler(req):
            requests.append(json.loads(req.content)); scenario['n']+=1; n=scenario['n']
            if n==1:return httpx.Response(429,request=req,json={'detail':marker})
            if n==2:raise httpx.ReadTimeout(marker,request=req)
            content = '{bad'+marker if n==3 else json.dumps({**READY,'parameters':{'student_id':marker}}) if n==4 else json.dumps(READY,ensure_ascii=False)
            return httpx.Response(200,request=req,json={'id':'mock','object':'chat.completion','created':1,'model':'mock','usage':{'prompt_tokens':30,'completion_tokens':20,'total_tokens':50},'choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':content}}]})
        planner=NaturalDeepSeekPlanner(api_key=key,test_transport=httpx.MockTransport(handler)); self.addCleanup(planner.close)
        buf=io.StringIO(); h=logging.StreamHandler(buf); log=logging.getLogger('jinbi.demo.diagnostics'); old_level,old_propagate=log.level,log.propagate; log.addHandler(h); log.setLevel(logging.WARNING); log.propagate=False
        self.addCleanup(log.removeHandler,h); self.addCleanup(setattr,log,'level',old_level); self.addCleanup(setattr,log,'propagate',old_propagate)
        results=[planner.check(Q) for _ in range(6)]
        self.assertTrue(all(x is None for x in results[:4])); self.assertIsNotNone(results[4]);self.assertIsNone(results[5])
        self.assertEqual(len(requests),5); self.assertEqual(requests[0]['thinking'],{'type':'disabled'}); self.assertEqual(requests[0]['max_tokens'],512)
        lines=buf.getvalue().splitlines(); records=[json.loads(line.removeprefix('demo_diagnostic ')) for line in lines]
        self.assertEqual([r['category'] for r in records],['http_error','timeout','invalid_json','invalid_schema','ok','request_limit'])
        self.assertEqual(records[0]['http_status'],429); self.assertEqual(records[1]['request_count'],2)
        self.assertEqual(records[4]['usage'],{'prompt_tokens':30,'completion_tokens':20,'total_tokens':50})
        self.assertTrue(records[4]['schema_valid']); self.assertTrue(records[4]['source_valid']); self.assertEqual(records[4]['encoding'],'identity')
        self.assertEqual(records[4]['suggestion']['tool'],'get_student_hours');self.assertEqual(records[4]['suggestion']['parameter_keys'],['student_name','subject'])
        for secret in (Q,key,marker): self.assertNotIn(secret,buf.getvalue())

    def test_real_transport_wiring_preserves_budget_across_planners(self):
        root=Path(__file__).resolve().parents[1] / 'evidence'
        calls=[]
        def mock_wire(_transport, request):
            calls.append(request)
            return httpx.Response(200,request=request,json={'id':'mock','object':'chat.completion','created':1,'model':'mock','choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(READY,ensure_ascii=False)}}]})
        with tempfile.TemporaryDirectory(dir=root) as tmp, \
             patch('app.services.demo_deepseek_client.REAL_BUDGET_PATH',Path(tmp)/'budget.sqlite3'), \
             patch.object(httpx.HTTPTransport,'handle_request',new=mock_wire):
            first=NaturalDeepSeekPlanner(api_key='synthetic-key'); self.addCleanup(first.close)
            self.assertTrue(all(first.check(Q) is not None for _ in range(3)))
            first.close()
            second=NaturalDeepSeekPlanner(api_key='synthetic-key'); self.addCleanup(second.close)
            self.assertTrue(all(second.check(Q) is not None for _ in range(2)))
            self.assertEqual(second.transport.budget.status(), (5, 100))
            self.assertEqual(len(calls),5)

    def test_status_and_truncation_diagnostics(self):
        buf=io.StringIO(); handler_log=logging.StreamHandler(buf); log=logging.getLogger('jinbi.demo.diagnostics')
        old_level,old_propagate=log.level,log.propagate; log.addHandler(handler_log); log.setLevel(logging.WARNING); log.propagate=False
        self.addCleanup(log.removeHandler,handler_log); self.addCleanup(setattr,log,'level',old_level); self.addCleanup(setattr,log,'propagate',old_propagate)
        for status in (401,403,429):
            calls=[]
            def respond(req):
                calls.append(1); return httpx.Response(status,request=req,json={'error':'synthetic-secret-marker'})
            planner=NaturalDeepSeekPlanner(api_key='synthetic-key',test_transport=httpx.MockTransport(respond))
            self.assertIsNone(planner.check(Q)); self.assertEqual(len(calls),1); planner.close()
        def truncated(req):
            return httpx.Response(200,request=req,json={'id':'mock','object':'chat.completion','created':1,'model':'mock','choices':[{'index':0,'finish_reason':'length','message':{'role':'assistant','content':'partial-synthetic-marker'}}]})
        planner=NaturalDeepSeekPlanner(api_key='synthetic-key',test_transport=httpx.MockTransport(truncated)); self.assertIsNone(planner.check(Q)); planner.close()
        records=[json.loads(line.removeprefix('demo_diagnostic ')) for line in buf.getvalue().splitlines()]
        self.assertEqual([r['http_status'] for r in records],[401,403,429,200])
        self.assertEqual(records[-1]['finish_reason'],'length');self.assertEqual(records[-1]['category'],'incomplete_response')
        self.assertNotIn('synthetic-secret-marker',buf.getvalue());self.assertNotIn('partial-synthetic-marker',buf.getvalue())

    def test_tls_and_corrupt_budget_fail_closed(self):
        def tls(req):
            try: raise ssl.SSLError('synthetic-marker')
            except ssl.SSLError as err: raise httpx.ConnectError('synthetic-marker',request=req) from err
        planner=NaturalDeepSeekPlanner(api_key='synthetic-key',test_transport=httpx.MockTransport(tls))
        with self.assertLogs('jinbi.demo.diagnostics',level='WARNING') as logs:
            self.assertIsNone(planner.check(Q))
        planner.close(); self.assertIn('tls_error',logs.output[0]); self.assertNotIn('synthetic-marker',logs.output[0])
        root=Path(__file__).resolve().parents[1] / 'evidence'
        with tempfile.TemporaryDirectory(dir=root) as tmp:
            path=Path(tmp)/'invalid.sqlite3'; path.write_bytes(b'invalid sqlite file'); calls=[]
            t=RestrictedTransport(httpx.MockTransport(lambda req:calls.append(1)),budget=DurableBudget(path))
            with self.assertRaises(sqlite3.DatabaseError): t.handle_request(httpx.Request('POST','https://api.deepseek.com/chat/completions'))
            self.assertEqual(calls,[]);t.close()

    def test_input_rejection_does_not_consume_budget(self):
        planner=NaturalDeepSeekPlanner(api_key='synthetic-key',test_transport=httpx.MockTransport(lambda req:self.fail('must not send')))
        self.addCleanup(planner.close)
        self.assertIsNone(planner.check('sk-synthetic-secret'));self.assertEqual(planner.transport.count,0)
