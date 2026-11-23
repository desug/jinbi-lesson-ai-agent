"""Explicit local-only online demo launcher; secret is read from the console.

Only invoke after separate authorization for actual model requests.
"""
import getpass
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def main():
    args=sys.argv[1:]
    # Retain --rules for existing launch commands. Rules now load on every start.
    if args and args[0]=='--rules': args=args[1:]
    if len(args)>1 or (args and not args[0].isdigit()):
        raise SystemExit('usage: python app/demo_online_launcher.py [--rules] [port]')
    port=int(args[0]) if args else 18765
    if not 1024 <= port <= 65535: raise SystemExit('invalid port')
    key=getpass.getpass('Demo API key: ')
    if not key.strip(): raise SystemExit('API key required')
    # Select only OS necessities; never copy any parent secret into the child
    # configuration. This runs before importing application configuration.
    env={k:os.environ[k] for k in ('SYSTEMROOT','WINDIR','PATH','TEMP','TMP') if k in os.environ}
    env.update({'APP_ENV':'test','PYTHON_DOTENV_DISABLED':'1','DEEPSEEK_API_KEY':'',
                'JWT_SECRET_KEY':'isolated-d2-r1-test-only','PYTHONDONTWRITEBYTECODE':'1',
                'MYSQL_HOST':'127.0.0.1','MYSQL_USER':'offline_unused','MYSQL_PASSWORD':'offline_unused','MYSQL_DB':'offline_unused',
                'JINBI_DEMO_RULES_ENABLED':'1'})
    os.environ.clear();os.environ.update(env)
    sys.path.insert(0,str(ROOT))
    from app.services.demo_deepseek_client import NaturalDeepSeekPlanner
    from app.offline_demo_app import create_demo_app
    import uvicorn
    planner=NaturalDeepSeekPlanner(api_key=key,model='deepseek-flash')
    key=None
    try:
        used, limit = planner.transport.budget.status()
        print(f'DeepSeek API 预算：已用 {used}/{limit}，剩余 {limit - used} 次。')
        demo = create_demo_app(mode='deepseek_prepared', trusted_planner=planner, seed_approved_rules=True)
        uvicorn.run(demo, host='127.0.0.1', port=port, log_level='warning')
    finally:
        planner.close()
    return 0

if __name__=='__main__': raise SystemExit(main())
