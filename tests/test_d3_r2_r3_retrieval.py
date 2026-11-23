import json, os, unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.offline_demo_app import create_demo_app

class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,{'JINBI_DEMO_RULES_ENABLED':'1'});self.env.start()
        self.app=create_demo_app();self.c=TestClient(self.app);self.c.__enter__()
        def login(name,password):
            r=self.c.post('/auth/admin-login',json={'username':name,'password':password});self.assertEqual(r.status_code,200);return {'Authorization':'Bearer '+r.json()['token']}
        self.a=login('fictional-demo','fictional-demo-only');self.b=login('fictional-rule-reviewer','fictional-review-only')
        self.ids=[]
        for item in json.loads(Path('app/knowledge/fictional_drafts.json').read_text(encoding='utf-8-sig')):
            r=self.c.post('/admin/demo-rules',headers=self.b,json=item);self.assertEqual(r.status_code,200);self.ids.append(r.json()['id'])
    def tearDown(self): self.c.__exit__(None,None,None);self.env.stop()
    def query(self,q):
        r=self.c.post('/admin/ai/chat',headers=self.a,json={'query':q});self.assertEqual(r.status_code,200);return r.json()
    def approve(self,i): self.assertEqual(self.c.post(f'/admin/demo-rules/{self.ids[i]}/approve',headers=self.b).status_code,200)
    def test_queries_and_non_topic(self):
        self.approve(0);self.approve(1)
        rules = [
            ('DEMO-JINBI-LEAVE',
             '学员如需请假，应在课程开始前联系教务老师提出申请。经教务老师确认后，可以安排一次补课。',
             ['请假规则是什么？', '请假有什么规定？', '怎么请假？', '请假流程是什么？']),
            ('DEMO-JINBI-MAKEUP',
             '补课时间由教务老师与学员协商确定，具体安排请联系教务老师确认。',
             ['补课规则是什么？', '补课有什么规定？', '补课要求有哪些？', '如何安排补课？', '怎么安排补课？']),
        ]
        for rule_id, (doc, answer, queries) in zip(self.ids, rules):
            original = self.c.get(f'/admin/demo-rules/{rule_id}', headers=self.a).json()
            for q in queries:
                with self.subTest(q=q):
                    result = self.query(q)
                    self.assertEqual(result['status'], 'ok')
                    self.assertEqual(result['answer'], answer)
                    source, = result['citations']
                    self.assertEqual(source['document_id'], doc)
                    self.assertEqual(source['source_location']['rule_id'], rule_id)
                    self.assertEqual(source['rule_text'], original['text'])
                    title = original['category'] + '规则'
                    self.assertEqual(source['display_title'], title)
                    self.assertEqual(source['display_text'], original['text'].split(title + '：', 1)[1].split('；', 1)[0] + '。')
        for q in ('退费有什么规定？','教学规则有哪些？','无关的天气规则是什么？'):
            with self.subTest(q=q):
                result = self.query(q)
                self.assertEqual(result['status'], 'not_found')
                self.assertEqual(result['answer'], '目前没有找到相关规定。')
                self.assertEqual(result['citations'], [])
        both=self.query('请假和补课有什么规定？');self.assertEqual({c['document_id'] for c in both['citations']},{'DEMO-JINBI-LEAVE','DEMO-JINBI-MAKEUP'})

    def test_makeup_count_requires_an_approved_leave_rule(self):
        self.approve(1)
        self.assertEqual(self.query('请假后可以补几次课？')['citations'], [])
        self.approve(0)
        for query in ('请假后可补几次课？', '请假后可以补几次课？', '请假后能补课多少次？', '请假后能安排多少次补课？'):
            with self.subTest(query=query):
                result = self.query(query)
                self.assertEqual(result['answer'], '学员请假经教务老师确认后，可以安排一次补课。')
                source, = result['citations']
                self.assertEqual(source['source_location']['rule_id'], self.ids[0])
                self.assertIn('一次补课', source['display_text'])
    def test_draft_revocation_and_gate(self):
        self.approve(0)
        self.assertEqual(self.query('如何安排补课？')['citations'], [])
        self.approve(1)
        self.assertEqual(self.c.post(f'/admin/demo-rules/{self.ids[1]}/revoke',headers=self.b).status_code,200)
        self.assertEqual(self.query('补课有什么规定？')['citations'],[])
        with patch.dict(os.environ,{'JINBI_DEMO_RULES_ENABLED':'0'}):
            r=self.query('请假有什么规定？');self.assertEqual(r['status'],'no_approved_rules');self.assertEqual(r['citations'],[])
    def test_fallback_excludes_superseded_version(self):
        self.approve(1)
        original=self.c.get(f'/admin/demo-rules/{self.ids[1]}',headers=self.a).json()
        fields=('document_id','title','category','class_type','effective_date','text')
        replacement={k:original[k] for k in fields}
        replacement.update(version='2',supersedes_id=self.ids[1],text=original['text']+' 虚构更新。')
        self.assertEqual(self.c.post('/admin/demo-rules',headers=self.a,json=replacement).status_code,403)
        created=self.c.post('/admin/demo-rules',headers=self.b,json=replacement);self.assertEqual(created.status_code,200)
        new_id=created.json()['id']
        self.assertEqual(self.c.post(f'/admin/demo-rules/{new_id}/approve',headers=self.b).status_code,200)
        result=self.query('补课有什么规定？')
        self.assertEqual([(c['document_id'],c['version']) for c in result['citations']],[('DEMO-JINBI-MAKEUP','2')])
        self.assertEqual(result['answer'], '请查看下方依据，或联系教务老师确认。')
        self.assertEqual(result['citations'][0]['rule_text'], replacement['text'])
