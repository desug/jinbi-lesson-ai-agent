# Demo guide

## Offline

Install `requirements-dev.txt`, then run `python app/offline_demo_launcher.py --rules`. In another terminal run `python app/prepare_offline_demo_rules.py` to load and approve the bundled fictional rules. Open `http://127.0.0.1:18765/`. Sign in with the demo credentials displayed by the page. Ask `查询虚构小明剩余课时`, choose one candidate, then ask `数学`. For retrieval try `请假规则是什么` or `补课时间如何安排`. Try a write request to see it rejected. Data is synthetic and held in an isolated SQLite database for the process lifetime.

## Online DeepSeek

Run `python app/demo_online_launcher.py --rules` in a private local terminal, enter the API key at the hidden prompt, then use the same local UI. The launcher does not read `.env`, clears inherited secrets before app imports, limits the listener to loopback and constructs the trusted planner with the entered key. Requests go to DeepSeek and may incur charges. Do not capture the key in screenshots or logs. This guide does not authorize or imply an API request during repository checks.

## Media placeholders

Add only owner-provided, consented and redacted files under `docs/media/`: mini-program, agent home, multi-turn hours, RAG answer, architecture/tool-flow diagrams. Add the public demo video URL here after it exists. No screenshots or video are fabricated in this repository.

For a fresh-process localhost check, run `python tests/run_release_http_smoke.py`. The script starts the official offline launcher, prepares fictional rules, checks HTTP responses, and terminates the child process.
