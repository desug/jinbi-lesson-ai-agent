"""Start and stop the official offline launcher around a localhost HTTP check."""
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

from release_http_smoke import main as verify_http

ROOT = Path(__file__).resolve().parents[1]


def main():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    process = subprocess.Popen(
        [sys.executable, str(ROOT / 'app/offline_demo_launcher.py'), '--rules', str(port)],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        with httpx.Client(timeout=1, trust_env=False) as client:
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError('offline launcher exited before readiness')
                try:
                    if client.get(f'http://127.0.0.1:{port}/health').status_code == 200:
                        break
                except httpx.RequestError:
                    pass
                time.sleep(0.1)
            else:
                raise RuntimeError('offline launcher readiness timed out')
        subprocess.run([sys.executable, str(ROOT / 'app/prepare_offline_demo_rules.py'), str(port)],
                       cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
        verify_http(port)
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


if __name__ == '__main__':
    main()
