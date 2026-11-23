"""Explicitly prepare only the two owner-approved fictional rules via localhost HTTP.
Run after starting the offline demo with --rules. The online launcher prepares
the same fixtures during startup. This script never loads project config.
"""
import hashlib
import json
import sys
from pathlib import Path
import httpx

EXPECTED_SHA256 = 'b248e5c48e72e98b820c64ff326cef7ce299bf17643f91f61c3f521e88d73d41'
FIELDS = ('document_id','version','title','category','class_type','effective_date','text','supersedes_id')


def load_approved_drafts():
    raw = (Path(__file__).resolve().parents[1] / 'app/knowledge/fictional_drafts.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != EXPECTED_SHA256:
        raise ValueError('draft hash mismatch')
    drafts = json.loads(raw.decode('utf-8-sig'))
    if [draft['document_id'] for draft in drafts] != ['DEMO-JINBI-LEAVE', 'DEMO-JINBI-MAKEUP']:
        raise ValueError('draft inventory mismatch')
    return drafts


def main():
    if len(sys.argv)>2 or (len(sys.argv)==2 and not sys.argv[1].isdigit()):
        raise SystemExit('usage: python app/prepare_offline_demo_rules.py [port]')
    port=int(sys.argv[1]) if len(sys.argv)==2 else 18765
    if not 1024 <= port <= 65535: raise SystemExit('invalid port')
    try:
        drafts = load_approved_drafts()
    except ValueError as exc:
        raise SystemExit(f'{exc}; no request sent') from None
    with httpx.Client(base_url=f'http://127.0.0.1:{port}',timeout=10,trust_env=False) as client:
        page=client.get('/demo');page.raise_for_status()
        if page.headers.get('X-Demo-Mode') not in ('offline_deterministic', 'deepseek_prepared'):
            raise SystemExit('unexpected server')
        def login(name,password):
            r=client.post('/auth/admin-login',json={'username':name,'password':password});r.raise_for_status()
            return {'Authorization':'Bearer '+r.json()['token']}
        ordinary=login('fictional-demo','fictional-demo-only')
        reviewer=login('fictional-rule-reviewer','fictional-review-only')
        path='/admin/demo-rules'
        listing=client.get(path,headers=ordinary);listing.raise_for_status()
        if listing.json(): raise SystemExit('rules already exist; restart isolated demo before preparation')
        for draft in drafts:
            created=client.post(path,headers=reviewer,json=draft);created.raise_for_status(); row=created.json(); rid=row['id']
            expected={k:draft.get(k) for k in FIELDS}
            if row['state']!='draft' or any(row[k]!=v for k,v in expected.items()): raise RuntimeError('draft readback mismatch')
            denied=client.post(f'{path}/{rid}/approve',headers=ordinary)
            if denied.status_code!=403: raise RuntimeError('ordinary admin approval not denied')
            approved=client.post(f'{path}/{rid}/approve',headers=reviewer);approved.raise_for_status()
            detail=client.get(f'{path}/{rid}',headers=ordinary);detail.raise_for_status()
            history=client.get(f'{path}/{rid}/history',headers=ordinary);history.raise_for_status()
            canonical=json.dumps(expected,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf8')
            digest=hashlib.sha256(canonical).hexdigest(); events=history.json(); current=detail.json()
            if current['state']!='approved' or any(current[k]!=v for k,v in expected.items()) or not events or events[-1]['action']!='approved' or events[-1]['admin_id']!=3 or events[-1]['digest']!=digest:
                raise RuntimeError('approval readback mismatch')
            print(f"{draft['document_id']}: create={created.status_code} draft; ordinary={denied.status_code}; approve={approved.status_code}; detail={detail.status_code} approved; event={events[-1]['action']} admin=3 digest={digest}")
    return 0
if __name__=='__main__': raise SystemExit(main())
