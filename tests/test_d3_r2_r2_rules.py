import json, os, unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.offline_demo_app import create_demo_app
from app.main import app as normal

class RulesDemoTests(unittest.TestCase):
    def test_isolated_api_lifecycle(self):
        with patch.dict(os.environ,{'JINBI_DEMO_RULES_ENABLED':'1'}), TestClient(create_demo_app()) as c:
            path='/admin/demo-rules'
            self.assertEqual(c.get(path).status_code,401)
            def auth(name,password):
                r=c.post('/auth/admin-login',json={'username':name,'password':password});self.assertEqual(r.status_code,200);return {'Authorization':'Bearer '+r.json()['token']}
            ordinary=auth('fictional-demo','fictional-demo-only');reviewer=auth('fictional-rule-reviewer','fictional-review-only')
            drafts=json.loads(Path('app/knowledge/fictional_drafts.json').read_text(encoding='utf-8-sig'))
            ids=[]
            for draft in drafts:
                self.assertEqual(c.post(path,headers=ordinary,json=draft).status_code,403)
                created=c.post(path,headers=reviewer,json=draft);self.assertEqual(created.status_code,200);self.assertEqual(created.json()['state'],'draft');ids.append(created.json()['id'])
            before=c.post('/admin/ai/chat',headers=ordinary,json={'query':'请假规则是什么？'}).json();self.assertEqual(before['status'],'no_approved_rules');self.assertEqual(before['citations'],[])
            self.assertEqual(c.post(f'{path}/{ids[0]}/approve',headers=ordinary).status_code,403)
            for rid in ids:
                self.assertEqual(c.post(f'{path}/{rid}/approve',headers=reviewer).status_code,200)
                h=c.get(f'{path}/{rid}/history',headers=ordinary).json();self.assertEqual(h[-1]['action'],'approved');self.assertEqual(h[-1]['admin_id'],3)
            result=c.post('/admin/ai/chat',headers=ordinary,json={'query':'请假规则是什么？'}).json();self.assertIn('DEMO-JINBI-LEAVE',[x['document_id'] for x in result['citations']])
            self.assertEqual(c.post(f'{path}/{ids[0]}/revoke',headers=reviewer).status_code,200)
            result=c.post('/admin/ai/chat',headers=ordinary,json={'query':'请假规则是什么？'}).json();self.assertNotIn('DEMO-JINBI-LEAVE',[x['document_id'] for x in result['citations']])
            self.assertEqual(c.get(f'{path}/{ids[0]}/history',headers=ordinary).json()[-1]['action'],'revoked')
    def test_default_and_normal_do_not_expose_demo_page_or_rules(self):
        with patch.dict(os.environ,{'JINBI_DEMO_RULES_ENABLED':'0'}), TestClient(create_demo_app()) as demo, TestClient(normal) as prod:
            self.assertEqual(demo.get('/admin/demo-rules').status_code,404)
            self.assertEqual(prod.get('/demo').status_code,404)
