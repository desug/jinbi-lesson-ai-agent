"""Run only offline regressions in a sanitized child; never read local .env."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SUITES = [
    'test_d2_r1_demo', 'test_d2_r2_launcher', 'test_d3_r1_page',
    'test_offline_demo_planner', 'test_d3_r2_r2_rules',
    'test_d3_r2_r3_retrieval', 'test_d35_r1_write_intent', 'test_ui_upgrade_contract',
]

if __name__ == '__main__':
    env = {k: os.environ[k] for k in ('SYSTEMROOT', 'WINDIR', 'PATH', 'TEMP', 'TMP') if k in os.environ}
    env.update(APP_ENV='test', PYTHON_DOTENV_DISABLED='1', DEEPSEEK_API_KEY='',
               JWT_SECRET_KEY='isolated-d2-r1-test-only', PYTHONDONTWRITEBYTECODE='1',
               MYSQL_HOST='127.0.0.1', MYSQL_USER='offline_unused',
               MYSQL_PASSWORD='offline_unused', MYSQL_DB='offline_unused')
    code = '''
import sys, unittest
sys.path[:0] = [sys.argv[1], sys.argv[1] + '/tests']
import pymysql, openai
def forbidden(*a, **kw): raise AssertionError('External DB/model access forbidden in UI checks')
pymysql.connect = forbidden
openai.OpenAI = forbidden
suite = unittest.defaultTestLoader.loadTestsFromNames(sys.argv[2:])
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
'''
    result = subprocess.run([sys.executable, '-I', '-c', code, str(ROOT), *(sys.argv[1:] or SUITES)], cwd=ROOT, env=env, capture_output=True, text=True, encoding='utf8', errors='replace')
    output = ROOT/'output/ui-upgrade'
    output.mkdir(parents=True, exist_ok=True)
    (output/'offline-tests.log').write_text(result.stdout+'\n'+result.stderr, encoding='utf8')
    print(result.stderr)
    raise SystemExit(result.returncode)
