import unittest
from datetime import date
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.core.database import Base
import app.models
from app.models.admin import Admin
from app.models.demo_rules import DemoRule, DemoRuleEvent
from app.services.demo_rule_approval import digest
from app.services.demo_rule_search import search_approved


class DemoRetrievalEvaluation(unittest.TestCase):
    def test_fixed_30_question_set(self):
        engine=create_engine('sqlite:///:memory:'); Base.metadata.create_all(engine); db=sessionmaker(bind=engine)()
        db.add(Admin(id=1,username='eval',password_hash='x'))
        rules=[
          DemoRule(id=1,document_id='DEMO-VIP',version='1',title='VIP班型课时扣除规则',category='课时扣除',class_type='VIP',effective_date='2020-01-01',text='VIP班型每次课程扣除一课时，负课时不允许。',state='approved',owner_id=1),
          DemoRule(id=2,document_id='DEMO-LEAVE',version='1',title='请假补课规则',category='请假补课',class_type='小班',effective_date='2020-01-01',text='请假需要登记，补课由管理员确认安排。',state='approved',owner_id=1)]
        db.add_all(rules);db.flush()
        db.add_all([DemoRuleEvent(rule_id=r.id,admin_id=1,action='approved',digest=digest(r)) for r in rules]);db.commit()
        positive=[('VIP班型课时怎么扣','DEMO-VIP'),('VIP扣课规则是什么','DEMO-VIP'),('VIP一节课扣多少课时','DEMO-VIP'),('VIP班能不能负课时','DEMO-VIP'),('VIP负余额允许吗','DEMO-VIP'),('请假要登记吗','DEMO-LEAVE'),('请假后如何补课','DEMO-LEAVE'),('补课谁来安排','DEMO-LEAVE'),('小班请假补课办法','DEMO-LEAVE'),('管理员确认补课吗','DEMO-LEAVE'),('课时扣除 VIP','DEMO-VIP'),('不允许负课时 VIP','DEMO-VIP'),('VIP课程扣除一课时','DEMO-VIP'),('请假登记补课安排','DEMO-LEAVE'),('补课需要管理员确认','DEMO-LEAVE'),('VIP 班型 每次课程 扣除','DEMO-VIP'),('学生请假登记日期','DEMO-LEAVE'),('小班补课怎么安排','DEMO-LEAVE'),('负课时规则 VIP 班','DEMO-VIP'),('请假以后补课管理员安排','DEMO-LEAVE')]
        negative=['学生手机号是什么','数学老师叫什么','退款多久到账','校区地址在哪里','明天几点上课','学费怎么支付','如何重置密码','天气怎么样','英语教材哪一本','校长的私人电话']
        top1=top3=0
        for q,expected in positive:
            result=search_approved(db,q,top_k=3); ids=[c['document_id'] for c in result['citations']]
            top1 += bool(ids and ids[0]==expected); top3 += expected in ids
        false_hits=sum(search_approved(db,q)['status'] not in ('not_found','no_approved_rules') for q in negative)
        self.assertEqual((top1,top3,false_hits),(20,20,0), f'eval top1={top1}/20 top3={top3}/20 unsupported={false_hits}/10')
        db.close();engine.dispose()
