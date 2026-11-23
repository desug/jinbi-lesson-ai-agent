import unittest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.core.database import Base
import app.models
from app.models.admin import Admin
from app.models.ai_memory import AiConversation, AiMessage
from app.routers.admin_ai import owned_conversation, put_preferences, PreferencesRequest
from app.schemas.auth import TokenPayload


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.db.add_all([Admin(id=1, username='a', password_hash='x'), Admin(id=2, username='b', password_hash='x')])
        self.db.add(AiConversation(id=1, admin_id=1, title='test', context={}, summary='', messages=[AiMessage(role='user', content='test', result={})]))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_owner_isolation_and_message_deletion(self):
        with self.assertRaises(HTTPException) as e:
            owned_conversation(self.db, 2, 1)
        self.assertEqual(e.exception.status_code, 404)
        self.db.delete(owned_conversation(self.db, 1, 1))
        self.db.commit()
        self.assertEqual(self.db.query(AiMessage).count(), 0)

    def test_preference_requires_confirmation(self):
        with self.assertRaises(HTTPException):
            put_preferences(PreferencesRequest(grade='初二', confirmed=False), TokenPayload(sub='1', role='admin'), self.db)

if __name__ == '__main__':
    unittest.main()
