import unittest
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient
from app.core.database import Base
import app.models
from app.main import app
from app.utils.deps import get_db, get_current_admin
from app.schemas.auth import TokenPayload
from app.models.admin import Admin
from app.models.student import Student
from app.models.student_subject import StudentSubject
from test_function_chat import Client, call


class HttpConversationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        with self.Session() as db:
            db.add_all([Admin(id=1, username='admin', password_hash='x'), Admin(id=2, username='other', password_hash='x')])
            for i in (1, 2):
                s = Student(name='张三', phone=f'1380000000{i}', student_no=f'S{i}', grade='初二', class_type='VIP')
                s.subjects = [StudentSubject(subject_name='数学', total_hours=10, remaining_hours=i, deducted_hours=0), StudentSubject(subject_name='英语', total_hours=10, remaining_hours=i+3, deducted_hours=0)]
                db.add(s)
            db.commit()
        def get_test_db():
            with self.Session() as db: yield db
        app.dependency_overrides[get_db] = get_test_db
        app.dependency_overrides[get_current_admin] = lambda: TokenPayload(sub='1', role='admin')
        self.http = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.engine.dispose()

    def test_selection_and_followup(self):
        models = [Client([]), Client([[call('get_student_hours', '{"student_ref":"REF"}')], []]), Client([[call('get_student_hours', '{"student_ref":"REF"}')], []])]
        from app.services import ai_conversation_chat
        original = ai_conversation_chat.run_chat
        def mocked(db, query, client=None, confirmed_student_id=None):
            return original(db, query, client=models.pop(0), confirmed_student_id=confirmed_student_id)
        with patch.object(ai_conversation_chat, 'run_chat', side_effect=mocked):
            first = self.http.post('/admin/ai/chat', json={'query':'查询张三剩余多少数学课时？'}).json()
            self.assertEqual(len(first['candidates']), 2)
            cid = first['conversationId']
            second = self.http.post('/admin/ai/chat', json={'query':'第二个。','conversationId':cid}).json()
            self.assertEqual(second['data'][0]['result']['subjects'][0]['remainingHours'], 2)
            third = self.http.post('/admin/ai/chat', json={'query':'那他英语还有多少课时？','conversationId':cid}).json()
            self.assertEqual(third['data'][0]['result']['subjects'][0]['remainingHours'], 5)
            app.dependency_overrides[get_current_admin] = lambda: TokenPayload(sub='2', role='admin')
            self.assertEqual(self.http.post('/admin/ai/chat', json={'query':'他呢','conversationId':cid}).status_code, 404)

    def test_invalid_payload(self):
        self.assertEqual(self.http.post('/admin/ai/chat', json={'query':'x','selectedStudentId':9}).status_code, 422)

    def test_validation_error_does_not_echo_personal_input(self):
        response = self.http.post('/admin/ai/chat', json={'query': '张三13800000001' * 30})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn('13800000001', response.text)

    def test_student_forbidden(self):
        from app.utils.deps import get_current_token_payload
        del app.dependency_overrides[get_current_admin]
        app.dependency_overrides[get_current_token_payload] = lambda: TokenPayload(sub='1', role='student')
        self.assertEqual(self.http.post('/admin/ai/chat', json={'query': '查询'}).status_code, 403)

    def test_invalid_tool_arguments(self):
        from app.services import ai_conversation_chat
        original = ai_conversation_chat.run_chat
        client = Client([[call('get_student_hours', '{"student_id":"invalid"}')]])
        with patch.object(ai_conversation_chat, 'run_chat', side_effect=lambda db, query, **kwargs: original(db, query, client=client)):
            response = self.http.post('/admin/ai/chat', json={'query': '查询S1数学课时'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'error')
