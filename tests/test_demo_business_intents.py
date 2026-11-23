import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from app.offline_demo_app import create_demo_app
from app.services.demo_deepseek_client import NaturalDeepSeekPlanner
from test_d4_r2_p3_mock_online import MISSING, READY


CHAT = '/admin/ai/chat'
RULES = '/admin/demo-rules'
DRAFTS = Path(__file__).resolve().parents[1] / 'evidence' / 'd3_r2_fictional_drafts.json'


class DemoBusinessIntentTests(unittest.TestCase):
    def login(self, client, username='fictional-demo', password='fictional-demo-only'):
        reply = client.post('/auth/admin-login', json={'username': username, 'password': password})
        self.assertEqual(reply.status_code, 200)
        return {'Authorization': 'Bearer ' + reply.json()['token']}

    def chat(self, client, auth, query, **fields):
        reply = client.post(CHAT, headers=auth, json={'query': query, **fields})
        self.assertEqual(reply.status_code, 200)
        return reply.json()

    def test_approved_rule_content_and_sources_in_both_modes(self):
        def reject_model_call(request):
            self.fail('Local rule queries must not contact the model')

        drafts = json.loads(DRAFTS.read_text(encoding='utf-8-sig'))
        planner = NaturalDeepSeekPlanner(api_key='synthetic-only', test_transport=httpx.MockTransport(reject_model_call))
        self.addCleanup(planner.close)
        cases = (
            ('补课有什么规定？', 1, '补课时间由教务老师与学员协商确定，具体安排请联系教务老师确认。'),
            ('请假后可补几次课？', 0, '学员请假经教务老师确认后，可以安排一次补课。'),
            ('补课时间怎么确定？', 1, '补课时间由教务老师与学员协商确定。'),
            ('如何确定补课时间？', 1, '补课时间由教务老师与学员协商确定。'),
        )
        responses = []
        for options in ({}, {'mode': 'deepseek_prepared', 'trusted_planner': planner, 'seed_approved_rules': True}):
            with self.subTest(mode=options.get('mode', 'offline')), \
                    patch.dict(os.environ, {'JINBI_DEMO_RULES_ENABLED': '1'}), \
                    TestClient(create_demo_app(**options)) as client:
                admin = self.login(client)
                if not options:
                    reviewer = self.login(client, 'fictional-rule-reviewer', 'fictional-review-only')
                    for draft in drafts:
                        created = client.post(RULES, headers=admin, json=draft)
                        self.assertEqual(created.status_code, 200)
                        rule_id = created.json()['id']
                        self.assertEqual(client.post(f'{RULES}/{rule_id}/approve', headers=reviewer).status_code, 200)

                listing = client.get(RULES, headers=admin)
                self.assertEqual(listing.status_code, 200)
                rows = listing.json()
                self.assertEqual([(row['document_id'], row['state'], row['text']) for row in rows],
                                 [(draft['document_id'], 'approved', draft['text']) for draft in drafts])

                cid = None
                mode_responses = []
                for query, index, expected in cases:
                    result = self.chat(client, admin, query, **({'conversationId': cid} if cid else {}))
                    self.assertEqual(result['status'], 'ok')
                    cid = cid or result['conversationId']
                    self.assertEqual(result['conversationId'], cid)
                    self.assertEqual(result['answer'], expected)
                    self.assertEqual(result['data'], [])
                    rule_id = rows[index]['id']
                    draft = drafts[index]
                    self.assertEqual(result['matched_rules'], [f'demo-db:{rule_id}'])
                    source, = result['citations']
                    self.assertEqual(source['document_id'], draft['document_id'])
                    self.assertEqual(source['rule_text'], draft['text'])
                    self.assertEqual(source['display_title'], draft['category'] + '规则')
                    self.assertNotRegex(source['display_text'], r'参赛演示|任何真实机构制度|demo-db')
                    self.assertEqual(source['source_location']['rule_id'], rule_id)
                    mode_responses.append((result['answer'], source))
                responses.append(mode_responses)

                missing = self.chat(client, admin, '转班有什么规定？', conversationId=cid)
                self.assertEqual(missing['status'], 'not_found')
                self.assertEqual(missing['citations'], [])
                self.assertEqual(missing['answer'], '目前没有找到相关规定。')
        self.assertEqual(len(responses), 2)
        self.assertEqual(responses[0], responses[1])
        self.assertEqual(planner.transport.count, 0)

    def test_punctuation_and_rule_query_with_pending_selection(self):
        requests = []

        def model_response(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {
                'role': 'assistant', 'content': json.dumps(READY)}}]})

        with patch.dict(os.environ, {'JINBI_DEMO_RULES_ENABLED': '0'}), TestClient(create_demo_app(
                mode='deepseek_online', mock_transport=httpx.MockTransport(model_response))) as client:
            auth = self.login(client)
            first = self.chat(client, auth, '查询虚构小明数学剩余课时')
            cid = first['conversationId']
            prompt = self.chat(client, auth, '?', conversationId=cid)
            self.assertEqual(prompt['status'], 'clarification')
            self.assertEqual(prompt['candidates'], first['candidates'])
            self.assertIn('请说明', prompt['answer'])
            rule = self.chat(client, auth, '请假有什么规定？', conversationId=cid)
            self.assertEqual(rule['status'], 'no_approved_rules')
            self.assertEqual(rule['candidates'], [])
            self.assertEqual(len(requests), 1)

    def test_generic_scope_clarification_then_current_turn_and_confirmed_student(self):
        outputs = iter(({**MISSING, 'intent': 'clarification', 'parameters': {}},))
        requests = []

        def model_response(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {
                'role': 'assistant', 'content': json.dumps(next(outputs))}}]})

        planner = NaturalDeepSeekPlanner(api_key='synthetic-only', test_transport=httpx.MockTransport(model_response))
        self.addCleanup(planner.close)
        with TestClient(create_demo_app(mode='deepseek_prepared', trusted_planner=planner)) as client:
            auth = self.login(client)
            first = self.chat(client, auth, '查询虚构小明的剩余课时')
            self.assertEqual(len(first['candidates']), 2)
            cid = first['conversationId']
            second = self.chat(client, auth, '选择第二位候选', conversationId=cid)
            self.assertIn('科目', second['answer'])
            self.assertEqual(len(requests), 1)

            done = self.chat(client, auth, '数学', conversationId=cid)
            self.assertEqual(done['status'], 'complete')
            result = done['data'][0]['result']
            self.assertEqual(result['studentId'], first['candidates'][1]['studentId'])
            self.assertEqual(result['subjects'][0]['remainingHours'], 10)
            self.assertEqual(len(requests), 1)
