"""Opt-in, in-memory SQLite-only DEMO endpoints."""
import os
import re
from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from rank_bm25 import BM25Okapi
from app.core.config import settings
from app.models.admin import Admin
from app.models.demo_rules import DemoRule, DemoRuleEvent
from app.schemas.auth import TokenPayload
from app.services.demo_rule_approval import approve, revoke, is_current
from app.services.rule_retrieval import tokens
from app.utils.deps import get_current_admin, get_db

router = APIRouter(prefix='/admin/demo-rules', tags=['DEMO'])


def context(request: Request, token: TokenPayload = Depends(get_current_admin), db: Session = Depends(get_db)):
    url = db.get_bind().url
    if (os.getenv('JINBI_DEMO_RULES_ENABLED') != '1' or settings.app_env.lower() not in ('test', 'testing', 'local')
        or url.drivername.split('+')[0] != 'sqlite' or url.database not in (None, '', ':memory:')):
        raise HTTPException(404, '功能未启用')
    admin = db.get(Admin, token.user_id) if token.user_id else None
    if not admin or token.sub != admin.username or admin.role != 'admin':
        raise HTTPException(403, '无权限访问')
    if admin.username in ('fictional-demo', 'fictional-demo-2') and request.method != 'GET' and request.url.path != '/admin/demo-rules/search':
        raise HTTPException(403, '演示查询账号无规则写入权限')
    return db, admin.id


class Draft(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    document_id: str = Field(pattern=r'^DEMO-[A-Za-z0-9_-]{1,70}$')
    version: str = Field(pattern=r'^[A-Za-z0-9._-]{1,40}$')
    title: str = Field(min_length=1, max_length=200)
    category: str = Field(min_length=1, max_length=80)
    class_type: str = Field(min_length=1, max_length=80)
    effective_date: date
    text: str = Field(min_length=1, max_length=10000)
    supersedes_id: int | None = Field(default=None, gt=0)


class Search(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    query: str = Field(min_length=1, max_length=500)
    class_type: str = Field(default='', max_length=80)
    category: str = Field(default='', max_length=80)
    top_k: int = Field(default=3, ge=1, le=10)


def serialize(r):
    return {'id': r.id, 'document_id': r.document_id, 'version': r.version, 'title': r.title,
            'category': r.category, 'class_type': r.class_type, 'effective_date': r.effective_date,
            'text': r.text, 'state': r.state, 'supersedes_id': r.supersedes_id, 'demo': True}


@router.post('')
def create_rule(payload: Draft, ctx=Depends(context)):
    db, admin_id = ctx
    r = DemoRule(**payload.model_dump(exclude={'effective_date'}), effective_date=payload.effective_date.isoformat(), state='draft', owner_id=admin_id)
    db.add(r)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, '文档版本已存在')
    return serialize(r)


@router.get('')
def list_rules(ctx=Depends(context)):
    db, _ = ctx
    return [serialize(r) for r in db.scalars(select(DemoRule).order_by(DemoRule.id)).all()]


@router.post('/search')
def search_demo(payload: Search, ctx=Depends(context)):
    db, _ = ctx
    rules = [r for r in db.scalars(select(DemoRule)).all() if is_current(db, r) and
             (not payload.class_type or r.class_type == payload.class_type) and
             (not payload.category or r.category == payload.category)]
    result = {'status': 'no_approved_rules' if not rules else 'not_found', 'answer': '未找到已批准的 DEMO 规则。', 'citations': [], 'matched_rules': [], 'warnings': ['DEMO 虚构规则'], 'retrieval_metadata': {'mode': 'DEMO'}}
    if not rules:
        return result
    terms = tokens(payload.query)
    corpus = [tokens(r.title + ' ' + r.category + ' ' + r.class_type + ' ' + r.text) for r in rules]
    if not terms:
        return result
    scores = BM25Okapi(corpus).get_scores(terms)
    candidates = [i for i in range(len(rules)) if len(set(terms) & set(corpus[i])) >= 2]
    candidates.sort(key=lambda i: float(scores[i]) + len(set(terms) & set(corpus[i])), reverse=True)
    if not candidates:
        return result
    # Multiple matching documents in the same scope require human review.
    scopes = [(rules[i].category, rules[i].class_type) for i in candidates]
    review = len(scopes) != len(set(scopes))
    selected = candidates if review else candidates[:payload.top_k]
    citations = []
    for i in selected:
        r = rules[i]
        citations.append({'document_id': r.document_id, 'document_title': r.title, 'version': r.version,
                          'section': '正文', 'chunk_id': f'demo-db:{r.id}',
                          'source_location': {'kind': 'demo_sqlite', 'rule_id': r.id, 'line_start': 1, 'line_end': min(len(r.text.splitlines()), 1)},
                          'supporting_excerpt': r.text.splitlines()[0][:200]})
    result.update(status='needs_review' if review else 'ok', answer='请人工核对 DEMO 规则。' if review else '请参阅 DEMO 原文。', citations=citations, matched_rules=[c['chunk_id'] for c in citations])
    return result


@router.get('/{rule_id}')
def detail(rule_id: int, ctx=Depends(context)):
    db, _ = ctx
    rule = db.get(DemoRule, rule_id)
    if not rule: raise HTTPException(404, '规则不存在')
    return serialize(rule)


@router.get('/{rule_id}/history')
def history(rule_id: int, ctx=Depends(context)):
    db, _ = ctx
    if not db.get(DemoRule, rule_id): raise HTTPException(404, '规则不存在')
    return [{'action': e.action, 'admin_id': e.admin_id, 'digest': e.digest, 'created_at': e.created_at} for e in db.scalars(select(DemoRuleEvent).where(DemoRuleEvent.rule_id == rule_id).order_by(DemoRuleEvent.id)).all()]


@router.post('/{rule_id}/approve')
def approve_rule(rule_id: int, ctx=Depends(context)):
    db, admin_id = ctx
    return serialize(approve(db, admin_id, rule_id))


@router.post('/{rule_id}/revoke')
def revoke_rule(rule_id: int, ctx=Depends(context)):
    db, admin_id = ctx
    revoke(db, admin_id, rule_id)
    return {'state': 'revoked', 'demo': True}
