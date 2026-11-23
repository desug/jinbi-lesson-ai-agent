import unittest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.core.database import Base
import app.models
from app.models.admin import Admin
from app.models.demo_rules import DemoRule, DemoRulePermission, DemoRuleEvent
from app.services.demo_rule_approval import approve, revoke, is_current

class DemoApprovalTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.db.add_all([Admin(id=1, username='ordinary', password_hash='x'), Admin(id=2, username='reviewer', password_hash='x'), DemoRulePermission(admin_id=2, can_approve_rules=True)])
        self.db.add(DemoRule(id=1, document_id='DEMO-VIP', version='1', title='DEMO 规则', category='扣课', class_type='VIP', effective_date='2020-01-01', text='虚构规则：每次扣一课时。', owner_id=1))
        self.db.commit()
    def tearDown(self): self.db.close(); self.engine.dispose()
    def test_permission_and_audit(self):
        with self.assertRaises(HTTPException): approve(self.db, 1, 1)
        rule = approve(self.db, 2, 1)
        self.assertTrue(is_current(self.db, rule))
        event = self.db.query(DemoRuleEvent).one()
        self.assertEqual((event.admin_id, event.action), (2, 'approved'))
    def test_content_and_metadata_changes_invalidate(self):
        rule = approve(self.db, 2, 1)
        rule.text += '修改'
        self.assertFalse(is_current(self.db, rule))
        self.db.rollback()
        self.db.get(DemoRule, 1).version = '2'
        self.assertFalse(is_current(self.db, self.db.get(DemoRule, 1)))
    def test_replacement_and_revoke(self):
        old = approve(self.db, 2, 1)
        new = DemoRule(id=2, document_id='DEMO-VIP', version='2', title='DEMO 规则', category='扣课', class_type='VIP', effective_date='2021-01-01', text='虚构规则', owner_id=1, supersedes_id=1)
        self.db.add(new); self.db.commit()
        approve(self.db, 2, 2)
        self.assertFalse(is_current(self.db, old)); self.assertTrue(is_current(self.db, new))
        revoke(self.db, 2, 2)
        self.assertFalse(is_current(self.db, new))
        self.assertEqual(self.db.query(DemoRuleEvent).count(), 4)
    def test_wrong_scope_cannot_supersede(self):
        approve(self.db, 2, 1)
        self.db.add(DemoRule(id=2, document_id='DEMO-VIP', version='2', title='DEMO', category='扣课', class_type='一对二', effective_date='2021-01-01', text='虚构', owner_id=1, supersedes_id=1)); self.db.commit()
        with self.assertRaises(HTTPException): approve(self.db, 2, 2)
