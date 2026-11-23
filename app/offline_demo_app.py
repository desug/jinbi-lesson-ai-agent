"""Explicit isolated demonstration app. Deterministic planner is not an LLM.

No database URL or caller-supplied engine is accepted. Never mount on the
normal application; only synthetic records live for this app's lifespan.
"""
from contextlib import asynccontextmanager
import os
import json
import logging
import hashlib
import marshal
from uuid import uuid4
from pathlib import Path
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.core.database import Base
from app.core.config import settings
from app.core.security import get_password_hash
from app.models.admin import Admin
from app.models.student import Student
from app.models.student_subject import StudentSubject
from app.models.demo_rules import DemoRule, DemoRulePermission
from app.prepare_offline_demo_rules import load_approved_drafts
from app.routers.auth import router as auth_router
from app.routers.demo_rules import Draft, router as demo_rules_router
from app.routers.admin_ai import ChatRequest
from app.schemas.auth import TokenPayload
from app.services.ai_conversation_chat import chat
from app.services.common import parse_token_subject_id
from app.services.demo_rule_approval import approve, is_current
from app.services.offline_demo_planner import OfflineDemoPlanner
from app.utils.deps import get_current_admin, get_db
import app.models  # register tables

def create_demo_app(*, mode="offline_deterministic", mock_transport=None, trusted_planner=None,
                    seed_approved_rules=False):
    if mode == "offline_deterministic":
        if mock_transport is not None or trusted_planner is not None: raise ValueError("offline mode cannot accept online configuration")
        natural = None
    elif mode == "deepseek_online":
        from app.services.demo_mock_online import MockNaturalPlanner
        if trusted_planner is not None: raise ValueError("unexpected planner")
        natural = MockNaturalPlanner(mock_transport)
    elif mode == "deepseek_prepared":
        from app.services.demo_deepseek_client import NaturalDeepSeekPlanner
        if mock_transport is not None or type(trusted_planner) is not NaturalDeepSeekPlanner: raise ValueError("trusted planner required")
        natural = trusted_planner
    else:
        raise ValueError("unsupported demo mode")
    # Fingerprint loaded code objects, not source files that may have changed
    # since import. This identifies the isolated app without probing a model.
    code_objects = [create_demo_app.__code__]
    if natural is not None:
        from app.services.demo_mock_online import (MockNaturalPlanner, validate_source, online_chat,
                                                   SuggestionClient, _answer, _clarification)
        from app.services.demo_deepseek_client import RestrictedTransport, DurableBudget, NaturalDeepSeekPlanner
        code_objects.extend([MockNaturalPlanner.check.__code__, validate_source.__code__,
                             online_chat.__code__, SuggestionClient.create.__code__,
                             RestrictedTransport.handle_request.__code__, DurableBudget.consume.__code__,
                             NaturalDeepSeekPlanner.check.__code__, NaturalDeepSeekPlanner._record_diagnostic.__code__,
                             _answer.__code__, _clarification.__code__])
    revision = hashlib.sha256(b''.join(marshal.dumps(code) for code in code_objects)).hexdigest()[:16]
    # The ordinary app's JWT settings are process-global. Require an explicitly
    # isolated test process rather than overriding authentication in either app.
    if settings.app_env.lower() != 'test' or settings.jwt_secret_key != 'isolated-d2-r1-test-only':
        raise RuntimeError('isolated test-only authentication configuration required')
    rules_enabled = os.getenv('JINBI_DEMO_RULES_ENABLED') == '1'
    if seed_approved_rules and (mode != 'deepseek_prepared' or not rules_enabled):
        raise ValueError('bundled rules require the prepared online demo with rules enabled')

    @asynccontextmanager
    async def lifespan(app):
        engine=create_engine('sqlite://',poolclass=StaticPool,connect_args={'check_same_thread':False})
        if engine.url.drivername!='sqlite' or engine.url.database not in (None,'',':memory:') or type(engine.pool) is not StaticPool:
            engine.dispose(); raise RuntimeError('isolated in-memory SQLite required')
        try:
            Base.metadata.create_all(engine)
            sessions=sessionmaker(bind=engine,expire_on_commit=False)
            with sessions() as db:
                for admin_id,username in ((1,'fictional-demo'),(2,'fictional-demo-2')):
                    db.add(Admin(id=admin_id,username=username,password_hash=get_password_hash('fictional-demo-only')))
                if rules_enabled:
                    db.add(Admin(id=3,username='fictional-rule-reviewer',password_hash=get_password_hash('fictional-review-only')))
                    db.flush()
                    db.add(DemoRulePermission(admin_id=3,can_approve_rules=True))
                for i in (1,2):
                    s=Student(name='虚构小明',phone=f'DEMO-PHONE-{i:03}',student_no=f'DEMO{i}',grade='初一',class_type='VIP')
                    s.subjects=[StudentSubject(subject_name='数学',total_hours=12,deducted_hours=2,remaining_hours=8+i)]
                    db.add(s)
                db.commit()
                if seed_approved_rules:
                    # Seed this instance's SQLite using the same reviewed fixtures
                    # and approval service as the offline preparation script.
                    drafts = [Draft.model_validate(raw) for raw in load_approved_drafts()]
                    for draft in drafts:
                        rule = DemoRule(**draft.model_dump(mode='json'), state='draft', owner_id=1)
                        db.add(rule)
                        db.flush()
                        approved = approve(db, 3, rule.id)
                        if not is_current(db, approved):
                            raise RuntimeError('bundled demo rule is not current')
            app.state.demo_engine=engine
            app.state.demo_sessions=sessions
            yield
        finally:
            engine.dispose()

    from threading import RLock
    online_lock = RLock()
    demo=FastAPI(title=('离线虚构数据 DEMO — 非真实模型' if natural is None else '在线虚构数据 DEMO — 状态未验证'),lifespan=lifespan)
    @demo.get('/health')
    def demo_health():
        return {'status': 'ok', 'mode': mode}
    @demo.get('/demo', include_in_schema=False)
    def demo_page():
        page = Path(__file__).parent / 'offline_demo.html'
        if natural is None:
            return FileResponse(page, headers={'Cache-Control': 'no-store', 'X-Demo-Revision': revision,
                                               'X-Demo-Mode': mode})
        label = '模拟模型测试' if mode == 'deepseek_online' else '在线模式 · 连接状态未验证'
        html = page.read_text(encoding='utf8').replace('Jinbi 课时助手 · 离线演示', 'Jinbi 课时助手 · 虚构数据演示').replace('虚构数据 · 确定性离线演示', '虚构数据 · ' + label).replace('回答来自离线演示流程，并非真实大模型。', '使用受限的虚构数据；模型连接状态未验证。')
        html = html.replace('data-demo-mode="offline_deterministic"', f'data-demo-mode="{mode}"')
        return HTMLResponse(html, headers={'Cache-Control': 'no-store', 'X-Demo-Revision': revision,
                                          'X-Demo-Mode': mode})
    def demo_db():
        engine=demo.state.demo_engine
        if engine.url.drivername!='sqlite' or engine.url.database not in (None,'',':memory:') or type(engine.pool) is not StaticPool:
            raise RuntimeError('isolated in-memory SQLite required')
        with demo.state.demo_sessions() as db:
            if db.get_bind() is not engine: raise RuntimeError('database mismatch')
            yield db
    demo.dependency_overrides[get_db]=demo_db
    demo.include_router(auth_router)
    if rules_enabled:
        demo.include_router(demo_rules_router)
    @demo.post('/admin/ai/chat')
    def demo_chat(payload:ChatRequest, admin:TokenPayload=Depends(get_current_admin), db=Depends(get_db)):
        admin_id = parse_token_subject_id(admin)
        if natural is not None:
            from app.services.demo_mock_online import online_chat, DIAGNOSTIC_REQUEST_ID
            with online_lock:
                request_id = uuid4().hex
                token = DIAGNOSTIC_REQUEST_ID.set(request_id)
                record = {'request_id': request_id, 'category': 'invalid_suggestion',
                          'stage': 'business', 'http_status': 500, 'tool_calls': 0,
                          'revision': revision}
                try:
                    result = online_chat(db, admin_id, payload.query, payload.conversation_id,
                                         payload.selected_student_id, natural)
                    record['http_status'] = 200
                    record['tool_calls'] = min(4, sum(1 for t in result.get('trace', [])
                                                     if t.get('status') == 'ok'))
                    if result.get('status') == 'error':
                        code = result.get('errorCode')
                        if code == 'unsafe_request':
                            record.update(category='input_rejected', stage='input')
                        elif code and code.startswith('model_'):
                            diagnostic = getattr(natural, 'last_diagnostic', {})
                            record.update(category=diagnostic.get('category', 'invalid_suggestion'),
                                          stage=diagnostic.get('stage', 'planner'))
                        else:
                            record.update(category='tool_rejected', stage='tool')
                    else:
                        record.update(category='success', stage='business')
                    result['diagnosticId'] = request_id
                    return result
                except HTTPException as exc:
                    record.update(category='invalid_session' if exc.status_code == 404 else 'invalid_suggestion',
                                  stage='session', http_status=exc.status_code)
                    raise
                finally:
                    DIAGNOSTIC_REQUEST_ID.reset(token)
                    logging.getLogger('jinbi.demo.diagnostics').warning(
                        'demo_chat_diagnostic %s', json.dumps(record, sort_keys=True))
        return chat(db,admin_id,payload.query,payload.conversation_id,payload.selected_student_id,client=OfflineDemoPlanner(),demo_scope_followup=True)
    return demo
