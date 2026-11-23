"""Launch a synthetic offline demo in a fresh, sanitized child process."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def main():
    args=sys.argv[1:]
    rules_enabled=bool(args and args[0]=='--rules')
    if rules_enabled: args=args[1:]
    if len(args)>1 or (args and not args[0].isdigit()):
        raise SystemExit('usage: python app/offline_demo_launcher.py [--rules] [port]')
    port=int(args[0]) if args else 18765
    if not 1024 <= port <= 65535: raise SystemExit('invalid port')
    # Construct, never enumerate or print, potentially sensitive parent values.
    env={k:os.environ[k] for k in ('SYSTEMROOT','WINDIR','PATH','TEMP','TMP') if k in os.environ}
    env.update({'APP_ENV':'test','PYTHON_DOTENV_DISABLED':'1','DEEPSEEK_API_KEY':'',
                'JWT_SECRET_KEY':'isolated-d2-r1-test-only','PYTHONDONTWRITEBYTECODE':'1',
                'MYSQL_HOST':'127.0.0.1','MYSQL_USER':'offline_unused','MYSQL_PASSWORD':'offline_unused','MYSQL_DB':'offline_unused'})
    if rules_enabled: env['JINBI_DEMO_RULES_ENABLED']='1'
    code=('import sys; sys.path.insert(0,sys.argv[1]); '
          'from app.offline_demo_app import create_demo_app; import uvicorn; '
          'uvicorn.run(create_demo_app(),host="127.0.0.1",port=int(sys.argv[2]),log_level="warning")')
    return subprocess.call([sys.executable,'-I','-c',code,str(ROOT),str(port)],cwd=str(ROOT),env=env)

if __name__=='__main__': raise SystemExit(main())
