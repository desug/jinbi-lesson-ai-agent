"""DEMO rule approval operations; explicitly gated to isolated SQLite databases."""
import hashlib
import json
from datetime import date
from fastapi import HTTPException
from sqlalchemy import select, update
from app.models.admin import Admin
from app.models.demo_rules import DemoRule, DemoRuleEvent, DemoRulePermission


def require_demo_db(db):
    if db.get_bind().dialect.name != 'sqlite':
        raise HTTPException(403, 'DEMO 仅允许隔离 SQLite')


def require_approver(db, admin_id):
    require_demo_db(db)
    admin = db.get(Admin, admin_id)
    permission = db.get(DemoRulePermission, admin_id)
    if not admin or not permission or not permission.can_approve_rules:
        raise HTTPException(403, '没有规则审批权限')


def digest(rule):
    payload = {k: getattr(rule, k) for k in ('document_id', 'version', 'title', 'category', 'class_type', 'effective_date', 'text', 'supersedes_id')}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()


def approve(db, admin_id, rule_id):
    require_approver(db, admin_id)
    rule = db.get(DemoRule, rule_id)
    if not rule or rule.state != 'draft':
        raise HTTPException(409, '规则不存在或不是草稿')
    try:
        date.fromisoformat(rule.effective_date)
    except ValueError:
        raise HTTPException(422, '生效日期无效')
    old = None
    if rule.supersedes_id:
        old = db.get(DemoRule, rule.supersedes_id)
        if (not old or not is_current(db, old) or old.document_id != rule.document_id
            or old.category != rule.category or old.class_type != rule.class_type
            or date.fromisoformat(rule.effective_date) < date.fromisoformat(old.effective_date)):
            raise HTTPException(409, '替代范围或时间不合法')
        # Future-dated replacements need scheduling; do not prematurely retire old rules.
        if date.fromisoformat(rule.effective_date) > date.today():
            raise HTTPException(409, '暂不支持未来生效的替代')
    try:
        result = db.execute(
            update(DemoRule).where(DemoRule.id == rule_id, DemoRule.state == 'draft')
            .values(state='approved').execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            raise HTTPException(409, '规则不存在或不是草稿')
        db.refresh(rule)
        if old is not None:
            result = db.execute(update(DemoRule).where(DemoRule.id == old.id, DemoRule.state == 'approved')
                                .values(state='superseded').execution_options(synchronize_session=False))
            if result.rowcount != 1:
                raise HTTPException(409, '替代范围或时间不合法')
            db.refresh(old)
            db.add(DemoRuleEvent(rule_id=old.id, admin_id=admin_id, action='superseded', digest=digest(old)))
        db.add(DemoRuleEvent(rule_id=rule.id, admin_id=admin_id, action='approved', digest=digest(rule)))
        db.commit()
    except Exception:
        db.rollback()
        raise
    return rule


def revoke(db, admin_id, rule_id):
    require_approver(db, admin_id)
    rule = db.get(DemoRule, rule_id)
    if not rule or rule.state != 'approved':
        raise HTTPException(409, '规则不存在或未批准')
    try:
        result = db.execute(update(DemoRule).where(DemoRule.id == rule_id, DemoRule.state == 'approved')
                            .values(state='revoked').execution_options(synchronize_session=False))
        if result.rowcount != 1:
            raise HTTPException(409, '规则不存在或未批准')
        db.refresh(rule)
        db.add(DemoRuleEvent(rule_id=rule.id, admin_id=admin_id, action='revoked', digest=digest(rule)))
        db.commit()
    except Exception:
        db.rollback()
        raise


def is_current(db, rule):
    if rule.state != 'approved' or date.fromisoformat(rule.effective_date) > date.today():
        return False
    event = db.scalar(select(DemoRuleEvent).where(DemoRuleEvent.rule_id == rule.id).order_by(DemoRuleEvent.id.desc()))
    return bool(event and event.action == 'approved' and event.digest == digest(rule))
