"""B1: isolated file SQLite concurrency evidence; no production DB or network."""
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from fastapi import Depends, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker
from app.core.database import Base
from app.core.config import settings
from app.core.security import create_access_token
from app.main import app
from app.models.admin import Admin
from app.models.demo_rules import DemoRule, DemoRuleEvent, DemoRulePermission
from app.models.student import Student
from app.models.student_subject import StudentSubject
from app.models.lesson_record import LessonRecord
from app.models.payment import Payment
from app.models.ai_memory import AiConversation, AiMessage
from app.routers.demo_rules import context
from app.services.demo_rule_approval import digest, is_current
from app.utils.deps import get_current_admin, get_db


class B1HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='jinbi-b1-')
        self.addCleanup(self.temp.cleanup)
        self.engine = create_engine(URL.create('sqlite', database=self.temp.name + '/test.sqlite'), connect_args={'check_same_thread': False, 'timeout': 5})
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.barrier = None
        self.read_hook = None
        self.reads = []
        owner = self
        class ObservedSession(Session):
            def get(self, entity, ident, *args, **kwargs):
                obj = super().get(entity, ident, *args, **kwargs)
                if entity is DemoRule and owner.read_hook is not None:
                    owner.read_hook(self, ident, obj)
                if entity is DemoRule and owner.barrier is not None:
                    owner.reads.append((threading.current_thread().name, obj.state if obj else None, time.perf_counter_ns(), id(self.connection().connection.driver_connection)))
                    owner.barrier.wait(timeout=8)
                return obj
        self.Session = sessionmaker(bind=self.engine, class_=ObservedSession)
        with self.Session() as db:
            db.add_all([Admin(id=1, username='fictional', password_hash='unused', role='admin'), DemoRulePermission(admin_id=1, can_approve_rules=True)])
            db.add(Student(id=50, name='虚构学员', phone='13900000050', student_no='DEMO50', grade='初一', class_type='VIP'))
            db.flush()
            db.add_all([StudentSubject(student_id=50, subject_name='数学', total_hours=10, remaining_hours=8, deducted_hours=2), LessonRecord(student_id=50, subject_name='数学', change_type='deduct', hours=2, amount=0, remaining_hours=8, operator_name='demo', remark='fictional'), Payment(student_id=50, amount=100, pay_type='demo', remark='fictional')])
            db.commit()
        def db_override():
            with self.Session() as db:
                yield db
        def file_context(token=Depends(get_current_admin), db=Depends(get_db)):
            # Test-only replacement of the in-memory-only gate; preserve identity checks.
            self.assertEqual(db.get_bind().url.drivername, 'sqlite')
            admin = db.get(Admin, token.user_id) if token.user_id else None
            if not admin or token.sub != admin.username or admin.role != 'admin':
                raise HTTPException(403, '无权限访问')
            return db, admin.id
        self.previous = dict(app.dependency_overrides)
        self.addCleanup(lambda: (app.dependency_overrides.clear(), app.dependency_overrides.update(self.previous)))
        app.dependency_overrides[get_db] = db_override
        app.dependency_overrides[context] = file_context
        secret = patch.object(settings, 'jwt_secret_key', 'B1_TEST_ONLY_SECRET_KEY_123456789')
        secret.start(); self.addCleanup(secret.stop)
        self.headers = {'Authorization': 'Bearer ' + create_access_token('fictional', 'admin', extra_claims={'user_id':1})}
        self.client = TestClient(app, raise_server_exceptions=False)
        self.addCleanup(self.client.close)

    def snapshot(self):
        models = (Student, StudentSubject, LessonRecord, Payment, DemoRule, DemoRuleEvent, DemoRulePermission, AiConversation, AiMessage)
        with self.Session() as db:
            return {m.__tablename__: [tuple(getattr(row, a.key) for a in m.__mapper__.column_attrs) for row in db.scalars(select(m).order_by(*m.__mapper__.primary_key)).all()] for m in models}

    def create(self, name):
        r = self.client.post('/admin/demo-rules', headers=self.headers, json={'document_id':'DEMO-'+name,'version':'1','title':'VIP课时规则','category':'扣课','class_type':'VIP','effective_date':'2020-01-01','text':'VIP班型扣除一课时。'})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()['id']

    def info(self, rid):
        with self.Session() as db:
            rule = db.get(DemoRule, rid)
            events = db.scalars(select(DemoRuleEvent).where(DemoRuleEvent.rule_id == rid).order_by(DemoRuleEvent.id)).all()
            return {'state':rule.state, 'version':rule.version, 'digest':digest(rule), 'current':is_current(db, rule), 'events':[(e.id,e.action,e.admin_id,str(e.created_at),e.digest) for e in events]}

    def unchanged(self, before, after):
        for key in ('students','student_subjects','lesson_records','payments','demo_rule_permissions','ai_conversations','ai_messages'):
            self.assertEqual(before[key], after[key], key)

    def test_repeat_approval(self):
        rid = self.create('REPEATAPPROVE'); before = self.snapshot(); initial = self.info(rid)
        first = self.client.post(f'/admin/demo-rules/{rid}/approve', headers=self.headers)
        middle = self.info(rid); snap = self.snapshot()
        second = self.client.post(f'/admin/demo-rules/{rid}/approve', headers=self.headers)
        final = self.info(rid); self.assertEqual((first.status_code,second.status_code),(200,409))
        self.assertEqual(initial['events'], []); self.assertEqual(middle,final); self.assertEqual(snap,self.snapshot())
        self.assertEqual(final['state'],'approved'); self.assertEqual(final['version'],'1'); self.assertTrue(final['current'])
        self.assertEqual([e[1] for e in final['events']],['approved']); self.assertEqual(final['events'][0][4],final['digest'])
        response = self.client.post('/admin/demo-rules/search',headers=self.headers,json={'query':'VIP课时规则'})
        self.assertIn(f'demo-db:{rid}',response.json()['matched_rules']); self.unchanged(before,self.snapshot())

    def test_repeat_revoke(self):
        rid = self.create('REPEATREVOKE'); before = self.snapshot()
        self.assertEqual(self.client.post(f'/admin/demo-rules/{rid}/approve',headers=self.headers).status_code,200)
        first = self.client.post(f'/admin/demo-rules/{rid}/revoke',headers=self.headers)
        middle = self.info(rid); snap = self.snapshot()
        second = self.client.post(f'/admin/demo-rules/{rid}/revoke',headers=self.headers)
        self.assertEqual((first.status_code,second.status_code),(200,409)); self.assertEqual(middle,self.info(rid)); self.assertEqual(snap,self.snapshot())
        self.assertEqual(middle['state'],'revoked'); self.assertFalse(middle['current']); self.assertEqual([e[1] for e in middle['events']],['approved','revoked'])
        self.assertTrue(all(e[4] == middle['digest'] for e in middle['events'])); self.unchanged(before,self.snapshot())
        self.assertFalse(self.client.post('/admin/demo-rules/search',headers=self.headers,json={'query':'VIP课时规则'}).json()['citations'])

    def race(self, rid, actions):
        self.reads = []; self.barrier = threading.Barrier(2, timeout=8)
        starts = threading.Barrier(2, timeout=8)
        def request(action):
            starts.wait(); start = time.perf_counter_ns()
            try:
                with TestClient(app, raise_server_exceptions=False) as client:
                    r = client.post(f'/admin/demo-rules/{rid}/{action}',headers=self.headers)
                    return {'action':action,'status':r.status_code,'body':r.text,'start':start,'end':time.perf_counter_ns()}
            except Exception as exc:
                return {'action':action,'exception':repr(exc),'start':start,'end':time.perf_counter_ns()}
        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(request, actions))
        finally:
            self.barrier = None
        self.assertEqual(len(self.reads),2, self.reads)
        self.assertEqual(len({r[3] for r in self.reads}),2, self.reads)
        self.assertLess(max(x['start'] for x in results),min(x['end'] for x in results))
        return results, list(self.reads)

    def test_concurrent_approval(self):
        issues = []
        for i in range(20):
            rid = self.create(f'RACE{i}'); before = self.snapshot()
            results, reads = self.race(rid, ('approve','approve'))
            info = self.info(rid); after = self.snapshot(); self.unchanged(before,after)
            print('B1_APPROVE', i, repr(results), repr(reads), repr(info), flush=True)
            statuses = sorted(x.get('status',0) for x in results)
            if statuses != [200,409] or info['state'] != 'approved' or [e[1] for e in info['events']] != ['approved']:
                issues.append((i,results,info))
            self.assertEqual(info['version'],'1'); self.assertTrue(info['current'])
            self.assertTrue(all(e[2] == 1 and e[4] == info['digest'] for e in info['events']))
        self.assertFalse(issues, 'Concurrent approval violates one approval event: '+repr(issues))

    def test_approval_event_insert_failure_rolls_back_atomic_update(self):
        from sqlalchemy import event
        rid = self.create('ROLLBACK')
        before = self.snapshot()
        observed = []
        def fail_event_insert(conn, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith('INSERT INTO DEMO_RULE_EVENTS'):
                observed.append(conn.exec_driver_sql('SELECT state FROM demo_rules WHERE id = ?', (rid,)).scalar_one())
                raise RuntimeError('TEST_B1_EVENT_FAILURE')
        event.listen(self.engine, 'before_cursor_execute', fail_event_insert)
        try:
            response = self.client.post(f'/admin/demo-rules/{rid}/approve', headers=self.headers)
        finally:
            event.remove(self.engine, 'before_cursor_execute', fail_event_insert)
        self.assertEqual(observed, ['approved'])
        self.assertEqual(response.status_code, 500)
        self.assertNotIn('TEST_B1_EVENT_FAILURE', response.text)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(self.info(rid)['state'], 'draft')
        self.assertEqual(self.info(rid)['events'], [])

    def test_approval_revoke_race(self):
        for i in range(10):
            rid = self.create(f'MIX{i}'); before = self.snapshot()
            results, reads = self.race(rid, ('approve','revoke'))
            info = self.info(rid); self.unchanged(before,self.snapshot())
            print('B1_MIX', i, repr(results), repr(reads), repr(info), flush=True)
            self.assertEqual([r[1] for r in reads],['draft','draft'])
            self.assertEqual({r['action']:r.get('status') for r in results},{'approve':200,'revoke':409})
            self.assertEqual(info['state'],'approved'); self.assertEqual(info['version'],'1'); self.assertTrue(info['current'])
            self.assertEqual([e[1] for e in info['events']],['approved']); self.assertEqual(info['events'][0][4],info['digest'])
            self.assertIn(f'demo-db:{rid}',self.client.post('/admin/demo-rules/search',headers=self.headers,json={'query':'VIP课时规则','top_k':10}).json()['matched_rules'])

    def candidate(self, document, version, old):
        response = self.client.post('/admin/demo-rules', headers=self.headers, json={
            'document_id':document, 'version':version, 'title':'VIP课时规则',
            'category':'扣课', 'class_type':'VIP', 'effective_date':'2020-01-01',
            'text':'VIP班型扣除一课时。', 'supersedes_id':old})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['id']

    def check_others(self, before, after, targets):
        self.unchanged(before, after)
        self.assertEqual([x for x in before['demo_rules'] if x[0] not in targets],
                         [x for x in after['demo_rules'] if x[0] not in targets])
        self.assertEqual([x for x in before['demo_rule_events'] if x[1] not in targets],
                         [x for x in after['demo_rule_events'] if x[1] not in targets])

    def b12_race(self, old_id, requests, candidate_ids=()):
        from app.services import demo_rule_approval as service
        barrier = threading.Barrier(2, timeout=8)
        start_barrier = threading.Barrier(2, timeout=8)
        reads, candidate_sessions = [], set()
        lock = threading.Lock()
        original_check = service.is_current
        def rendezvous(db, rule, where):
            with lock:
                reads.append((where, rule.id, rule.state, id(db), id(rule), id(db.connection().connection.driver_connection), time.perf_counter_ns()))
            barrier.wait(timeout=8)
        def hook(db, ident, obj):
            if ident in candidate_ids:
                with lock: candidate_sessions.add(id(db))
            elif ident == old_id and obj is not None:
                with lock: is_candidate = id(db) in candidate_sessions
                if not is_candidate:
                    rendezvous(db, obj, 'revoke_get')
        def check(db, rule):
            valid = original_check(db, rule)
            if rule.id == old_id:
                self.assertTrue(valid)
                rendezvous(db, rule, 'replacement_is_current')
            return valid
        def request(item):
            rid, action = item
            start_barrier.wait(timeout=8); start = time.perf_counter_ns()
            try:
                with TestClient(app, raise_server_exceptions=False) as client:
                    response = client.post(f'/admin/demo-rules/{rid}/{action}', headers=self.headers)
                    return {'id':rid,'action':action,'status':response.status_code,'body':response.text,'start':start,'end':time.perf_counter_ns()}
            except Exception as exc:
                return {'id':rid,'action':action,'exception':repr(exc),'start':start,'end':time.perf_counter_ns()}
        self.read_hook = hook
        try:
            with patch.object(service, 'is_current', side_effect=check):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    results = list(executor.map(request, requests))
        finally:
            self.read_hook = None
        self.assertEqual(len(reads), 2, (reads, results))
        self.assertEqual(len({r[3] for r in reads}),2)
        self.assertEqual(len({r[4] for r in reads}),2)
        self.assertEqual(len({r[5] for r in reads}),2)
        self.assertLess(max(r['start'] for r in results), min(r['end'] for r in results))
        return results, reads

    def test_b12_concurrent_revoke(self):
        issues = []
        for i in range(20):
            rid = self.create(f'REVOKE{i}')
            self.assertEqual(self.client.post(f'/admin/demo-rules/{rid}/approve',headers=self.headers).status_code,200)
            self.assertTrue(self.info(rid)['current'])
            self.assertIn(f'demo-db:{rid}',self.client.post('/admin/demo-rules/search',headers=self.headers,json={'query':'VIP课时规则','top_k':10}).json()['matched_rules'])
            before = self.snapshot()
            results, reads = self.b12_race(rid, [(rid,'revoke'),(rid,'revoke')])
            info = self.info(rid); after = self.snapshot(); self.check_others(before,after,{rid})
            search = self.client.post('/admin/demo-rules/search',headers=self.headers,json={'query':'VIP课时规则','top_k':10}).json()
            print('B12_REVOKE',i,repr(results),repr(reads),repr(info),repr(search['matched_rules']),flush=True)
            self.assertEqual([x[2] for x in reads],['approved','approved'])
            self.assertNotIn(f'demo-db:{rid}',search['matched_rules'])
            self.assertTrue(all(e[4] == info['digest'] and e[2] == 1 for e in info['events']))
            if sorted(r.get('status',0) for r in results) != [200,409] or info['state'] != 'revoked' or [e[1] for e in info['events']] != ['approved','revoked']:
                issues.append((i,results,info))
        self.assertFalse(issues, repr(issues))

    def test_b12_competing_successors(self):
        issues = []
        for i in range(20):
            name = f'NEXT{i}'; old = self.create(name)
            self.assertEqual(self.client.post(f'/admin/demo-rules/{old}/approve',headers=self.headers).status_code,200)
            a = self.candidate('DEMO-'+name,'2',old); b = self.candidate('DEMO-'+name,'3',old)
            before = self.snapshot(); results, reads = self.b12_race(old, [(a,'approve'),(b,'approve')], (a,b))
            infos = {rid:self.info(rid) for rid in (old,a,b)}
            with self.Session() as db: links = [(rid,db.get(DemoRule,rid).version,db.get(DemoRule,rid).supersedes_id) for rid in (old,a,b)]
            self.check_others(before,self.snapshot(),{old,a,b})
            search = self.client.post('/admin/demo-rules/search',headers=self.headers,json={'query':'VIP课时规则','top_k':10}).json()
            print('B12_SUCCESSORS',i,repr(results),repr(reads),repr(infos),repr(links),repr(search['matched_rules']),flush=True)
            self.assertEqual([x[2] for x in reads],['approved','approved'])
            self.assertEqual(links,[(old,'1',None),(a,'2',old),(b,'3',old)])
            self.assertTrue(all(e[4] == info['digest'] for info in infos.values() for e in info['events']))
            for rid, info in infos.items():
                self.assertEqual(f'demo-db:{rid}' in search['matched_rules'], info['current'])
            if sorted(r.get('status',0) for r in results) == [200,409]:
                loser = next(r['id'] for r in results if r['status'] == 409)
                self.assertEqual(infos[loser]['state'], 'draft')
                self.assertEqual(infos[loser]['events'], [])
            if sorted(r.get('status',0) for r in results) != [200,409] or sum(infos[x]['current'] for x in (a,b)) != 1 or [e[1] for e in infos[old]['events']] != ['approved','superseded']:
                issues.append((i,results,infos))
        self.assertFalse(issues,repr(issues))

    def test_b12_replacement_revoke_compete(self):
        issues = []
        for i in range(20):
            name=f'REPLACE{i}'; old=self.create(name)
            self.assertEqual(self.client.post(f'/admin/demo-rules/{old}/approve',headers=self.headers).status_code,200)
            new=self.candidate('DEMO-'+name,'2',old); before=self.snapshot()
            results, reads = self.b12_race(old, [(new,'approve'),(old,'revoke')], (new,))
            infos={rid:self.info(rid) for rid in (old,new)}; self.check_others(before,self.snapshot(),{old,new})
            with self.Session() as db:
                self.assertEqual((db.get(DemoRule, old).version, db.get(DemoRule, new).version, db.get(DemoRule, new).supersedes_id), ('1', '2', old))
            search=self.client.post('/admin/demo-rules/search',headers=self.headers,json={'query':'VIP课时规则','top_k':10}).json()
            print('B12_MIX',i,repr(results),repr(reads),repr(infos),repr(search['matched_rules']),flush=True)
            self.assertEqual({x[0] for x in reads},{'replacement_is_current','revoke_get'})
            self.assertTrue(all(x[2]=='approved' for x in reads))
            self.assertTrue(all(e[4] == info['digest'] for info in infos.values() for e in info['events']))
            for rid, info in infos.items():
                self.assertEqual(f'demo-db:{rid}' in search['matched_rules'], info['current'])
            statuses=sorted(r.get('status',0) for r in results)
            if statuses == [200,409]:
                winner = next(r['action'] for r in results if r['status'] == 200)
                if winner == 'revoke':
                    self.assertEqual((infos[old]['state'],infos[new]['state']), ('revoked','draft'))
                    self.assertEqual([e[1] for e in infos[old]['events']], ['approved','revoked'])
                    self.assertEqual(infos[new]['events'], [])
                else:
                    self.assertEqual((infos[old]['state'],infos[new]['state']), ('superseded','approved'))
                    self.assertEqual([e[1] for e in infos[old]['events']], ['approved','superseded'])
                    self.assertEqual([e[1] for e in infos[new]['events']], ['approved'])
            if statuses != [200,409]: issues.append((i,results,infos))
        self.assertFalse(issues,repr(issues))

    def test_b12_sequential_replacement_revoke_order(self):
        for first in ('revoke','replace'):
            old=self.create('ORDER'+first)
            self.assertEqual(self.client.post(f'/admin/demo-rules/{old}/approve',headers=self.headers).status_code,200)
            new=self.candidate('DEMO-ORDER'+first,'2',old)
            if first == 'revoke':
                one=self.client.post(f'/admin/demo-rules/{old}/revoke',headers=self.headers)
                two=self.client.post(f'/admin/demo-rules/{new}/approve',headers=self.headers)
                self.assertEqual((self.info(old)['state'],self.info(new)['state']),('revoked','draft'))
            else:
                one=self.client.post(f'/admin/demo-rules/{new}/approve',headers=self.headers)
                two=self.client.post(f'/admin/demo-rules/{old}/revoke',headers=self.headers)
                self.assertEqual((self.info(old)['state'],self.info(new)['state']),('superseded','approved'))
            self.assertEqual((one.status_code,two.status_code),(200,409))

    def _b13_insert_failure(self, replacement):
        from sqlalchemy import event
        old = self.create('ROLLREPLACE' if replacement else 'ROLLREVOKE')
        self.assertEqual(self.client.post(f'/admin/demo-rules/{old}/approve',headers=self.headers).status_code,200)
        new = self.candidate('DEMO-ROLLREPLACE','2',old) if replacement else None
        before = self.snapshot(); observed = []
        def inject(conn, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith('INSERT INTO DEMO_RULE_EVENTS'):
                states = conn.exec_driver_sql('SELECT id, state FROM demo_rules WHERE id IN (?, ?) ORDER BY id', (old, new or old)).all()
                observed.append(states)
                raise RuntimeError('TEST_B13_EVENT_FAILURE')
        event.listen(self.engine,'before_cursor_execute',inject)
        try:
            response=self.client.post(f'/admin/demo-rules/{new}/approve' if replacement else f'/admin/demo-rules/{old}/revoke',headers=self.headers)
        finally:
            event.remove(self.engine,'before_cursor_execute',inject)
        self.assertEqual(response.status_code,500); self.assertNotIn('TEST_B13_EVENT_FAILURE',response.text)
        self.assertEqual(observed, [[(old,'superseded'),(new,'approved')]] if replacement else [[(old,'revoked')]])
        self.assertEqual(before,self.snapshot())

    def test_b13_revoke_event_failure_rollback(self):
        self._b13_insert_failure(False)

    def test_b13_replacement_event_failure_rollback(self):
        self._b13_insert_failure(True)
