"""Page mode and real rule-response fixtures, with no model construction/calls."""
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.offline_demo_app import create_demo_app

ROOT = Path(__file__).resolve().parents[1]


class UiContractTests(unittest.TestCase):
    def test_server_supplied_modes_never_claim_success(self):
        with TestClient(create_demo_app()) as client:
            self.assertIn('虚构数据 · 确定性离线演示', client.get('/demo').text)
        from app.services.demo_mock_online import MockNaturalPlanner
        with patch.object(MockNaturalPlanner, '__init__', return_value=None):
            app = create_demo_app(mode='deepseek_online')
            with patch.object(MockNaturalPlanner, 'check') as check, TestClient(app) as client:
                html = client.get('/demo').text
                check.assert_not_called()
                self.assertIn('虚构数据 · 模拟模型测试', html)
                self.assertNotIn('>虚构数据 · 确定性离线演示', html)
        from app.services.demo_deepseek_client import NaturalDeepSeekPlanner
        # Exact trusted type for page-only rendering, without invoking __init__,
        # supplying any key, constructing a client, or touching a budget file.
        planner = object.__new__(NaturalDeepSeekPlanner)
        app = create_demo_app(mode='deepseek_prepared', trusted_planner=planner)
        with patch.object(planner, 'check') as check, TestClient(app) as client:
            html = client.get('/demo').text
            check.assert_not_called()
            self.assertIn('虚构数据 · 在线模式 · 连接状态未验证', html)
            self.assertNotIn('>虚构数据 · 确定性离线演示', html)
            self.assertNotIn('回答来自离线演示流程，并非真实大模型。', html)

    def test_real_approved_rule_response_for_browser_rendering(self):
        # Existing fictional drafts and approval APIs, confined to ephemeral SQLite.
        with patch.dict(os.environ, {'JINBI_DEMO_RULES_ENABLED':'1'}), TestClient(create_demo_app()) as client:
            def auth(name, password):
                result = client.post('/auth/admin-login', json={'username':name,'password':password})
                self.assertEqual(result.status_code, 200)
                return {'Authorization':'Bearer '+result.json()['token']}
            admin = auth('fictional-demo','fictional-demo-only')
            reviewer = auth('fictional-rule-reviewer','fictional-review-only')
            drafts = json.loads((ROOT/'app/knowledge/fictional_drafts.json').read_text(encoding='utf-8-sig'))
            for draft in drafts:
                result = client.post('/admin/demo-rules', headers=admin, json=draft)
                self.assertEqual(result.status_code, 200)
                self.assertEqual(client.post('/admin/demo-rules/'+str(result.json()['id'])+'/approve', headers=reviewer).status_code, 200)
            result = client.post('/admin/ai/chat', headers=admin, json={'query':'补课有什么规定？'})
            self.assertEqual(result.status_code, 200)
            data = result.json()
            self.assertEqual(data['status'], 'ok')
            self.assertEqual(data['citations'][0]['source_location']['kind'], 'demo_sqlite')
            self.assertTrue(data['citations'][0]['supporting_excerpt'])
            output = ROOT/'output/ui-upgrade'
            output.mkdir(parents=True, exist_ok=True)
            (output/'rules-response.test-only.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8')


if __name__ == '__main__':
    unittest.main()
