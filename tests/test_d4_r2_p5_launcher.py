import unittest, subprocess, sys, os, sqlite3, tempfile
from contextlib import closing
from pathlib import Path

class OnlineLaunchTests(unittest.TestCase):
    def test_fresh_process_isolated_before_import_without_request(self):
        root=Path(__file__).resolve().parents[1]
        code='''import os,sys,runpy,getpass,sqlite3
from pathlib import Path
from unittest.mock import patch
root=sys.argv[1]; sys.path.insert(0,root)
os.environ['PYTHON_DOTENV_DISABLED']='0'; os.environ['DEEPSEEK_API_KEY']='synthetic-parent-secret'
import uvicorn,httpx,pymysql
budget_fixture=Path(sys.argv[2])
real_connect=sqlite3.connect
def connect(database,*args,**kwargs):
 if Path(database)==Path(root)/'evidence/d4_r3_request_budget.sqlite3':
  database=budget_fixture
 return real_connect(database,*args,**kwargs)
def run(app,**kwargs):
 from app.core.config import settings
 from app.prepare_offline_demo_rules import load_approved_drafts
 from fastapi.testclient import TestClient
 assert settings.app_env=='test' and settings.deepseek_api_key==''
 assert os.environ['PYTHON_DOTENV_DISABLED']=='1'
 assert os.environ['JINBI_DEMO_RULES_ENABLED']=='1'
 assert kwargs['host']=='127.0.0.1'
 drafts=load_approved_drafts()
 for _ in range(2):
  with TestClient(app) as client:
   login=client.post('/auth/admin-login',json={'username':'fictional-demo','password':'fictional-demo-only'})
   assert login.status_code==200
   headers={'Authorization':'Bearer '+login.json()['token']}
   listing=client.get('/admin/demo-rules',headers=headers)
   assert listing.status_code==200
   rows=listing.json()
   assert len(rows)==len(drafts)
   for draft,row in zip(drafts,rows):
    assert row['state']=='approved' and all(row[field]==value for field,value in draft.items())
    history=client.get('/admin/demo-rules/'+str(row['id'])+'/history',headers=headers).json()
    assert len(history)==1 and history[0]['action']=='approved' and history[0]['admin_id']==3
with patch.object(sqlite3,'connect',side_effect=connect), patch.object(getpass,'getpass',return_value='synthetic-demo-key'), patch.object(uvicorn,'run',side_effect=run), patch.object(httpx.HTTPTransport,'handle_request',side_effect=AssertionError('network forbidden')), patch.object(pymysql,'connect',side_effect=AssertionError('mysql forbidden')):
 sys.argv=['demo_online_launcher.py']; runpy.run_path(os.path.join(root,'app','demo_online_launcher.py'),run_name='__main__')
'''
        env={k:os.environ[k] for k in ('SYSTEMROOT','WINDIR','PATH','TEMP','TMP') if k in os.environ}
        with tempfile.TemporaryDirectory() as tmp:
            budget = Path(tmp) / 'budget.sqlite3'
            with closing(sqlite3.connect(budget)) as db:
                db.execute('CREATE TABLE budget (id INTEGER PRIMARY KEY CHECK(id=1), used INTEGER NOT NULL CHECK(used BETWEEN 0 AND 10))')
                db.execute('INSERT INTO budget VALUES (1, 7)')
                db.commit()
            result=subprocess.run([sys.executable,'-I','-X','utf8','-c',code,str(root),str(budget)],cwd=root,env=env,capture_output=True,encoding='utf8',timeout=45)
            with closing(sqlite3.connect(budget)) as db:
                self.assertEqual(db.execute('SELECT used FROM budget').fetchone()[0], 7)
        self.assertEqual(result.returncode,0,msg=result.stderr)
        self.assertIn('已用 7/100，剩余 93 次',result.stdout)
        self.assertNotIn('synthetic-demo-key',result.stdout+result.stderr)
        self.assertNotIn('synthetic-parent-secret',result.stdout+result.stderr)
