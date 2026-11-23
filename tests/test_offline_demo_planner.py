import unittest
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient
from app.core.database import Base
from app.main import app
from app.models.student import Student
from app.models.student_subject import StudentSubject
from app.schemas.auth import TokenPayload
from app.services import ai_conversation_chat
from app.services.offline_demo_planner import OfflineDemoPlanner
from app.utils.deps import get_db, get_current_admin

class OfflinePlannerTests(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
        Base.metadata.create_all(self.engine); self.Session=sessionmaker(bind=self.engine)
        with self.Session() as db:
            for i in (1,2):
                s=Student(name='虚构小明',phone=f'1390000000{i}',student_no=f'DEMO{i}',grade='初一',class_type='VIP')
                s.subjects=[StudentSubject(subject_name='数学',total_hours=10,deducted_hours=2,remaining_hours=8+i)]
                db.add(s)
            db.commit()
        def fixture_db():
            with self.Session() as db: yield db
        app.dependency_overrides[get_db]=fixture_db
        app.dependency_overrides[get_current_admin]=lambda:TokenPayload(sub='1',role='admin')
        self.http=TestClient(app)
    def tearDown(self):
        app.dependency_overrides.clear(); self.engine.dispose()

    def test_default_is_degraded(self):
        result=self.http.post('/admin/ai/chat',json={'query':'虚构小明数学还剩多少节课？'}).json()
        self.assertEqual(result['status'],'degraded')

    def test_injected_planner_confirmation_and_real_sqlite_result(self):
        original=ai_conversation_chat.run_chat
        def isolated(db,query,client=None,confirmed_student_id=None):
            self.assertEqual(db.get_bind().url.drivername,'sqlite')
            self.assertIn(db.get_bind().url.database,(None,'',':memory:'))
            return original(db,query,client=OfflineDemoPlanner(),confirmed_student_id=confirmed_student_id)
        with patch.object(ai_conversation_chat,'run_chat',side_effect=isolated):
            first=self.http.post('/admin/ai/chat',json={'query':'虚构小明数学还剩多少节课？'}).json()
            self.assertEqual(first['status'],'clarification'); self.assertEqual(len(first['candidates']),2)
            second=self.http.post('/admin/ai/chat',json={'query':'第二个','conversationId':first['conversationId']}).json()
            self.assertEqual(second['status'],'complete')
            self.assertEqual(second['data'][0]['result']['subjects'][0]['remainingHours'],10)
    def test_unsupported_payload_fails_closed(self):
        with self.assertRaises(ValueError):
            OfflineDemoPlanner().create(model='test',messages=[{'role':'system'},{'role':'user','content':'{"student_id":1}'}],tools=[],tool_choice='auto',extra_body={})

    def test_missing_subject_followup_is_not_yet_supported(self):
        original=ai_conversation_chat.run_chat
        def isolated(db,query,client=None,confirmed_student_id=None):
            self.assertEqual(db.get_bind().url.drivername,'sqlite')
            self.assertIn(db.get_bind().url.database,(None,'',':memory:'))
            return original(db,query,client=OfflineDemoPlanner(),confirmed_student_id=confirmed_student_id)
        with patch.object(ai_conversation_chat,'run_chat',side_effect=isolated):
            first=self.http.post('/admin/ai/chat',json={'query':'查询虚构小明剩余课时'}).json()
            self.assertEqual(len(first['candidates']),2)
            second=self.http.post('/admin/ai/chat',json={'query':'第一个','conversationId':first['conversationId']}).json()
            self.assertEqual(second['status'],'clarification'); self.assertIn('科目',second['answer'])
            third=self.http.post('/admin/ai/chat',json={'query':'数学','conversationId':first['conversationId']}).json()
            self.assertEqual(third['status'],'clarification'); self.assertFalse(third['data'])
