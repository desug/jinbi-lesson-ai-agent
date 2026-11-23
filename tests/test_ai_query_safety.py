from decimal import Decimal
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.core.database import Base
import app.models  # register all models
from app.models.student import Student
from app.models.student_subject import StudentSubject
from app.schemas.ai import ParsedAiQuery
from app.services.ai_query_service import _detect_threshold, _execute_parsed_query


class QuerySafetyTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        for i, (name, grade, kind, hours) in enumerate([
            ('小王', '初二', 'VIP', '-1'), ('小王', '初三', 'VIP', '0'),
            ('小李', '初二', '小班', '-2'), ('小张', '初二', 'VIP', '0')], 1):
            student = Student(name=name, phone=f'1380000000{i}', student_no=f'S{i}', grade=grade, class_type=kind)
            student.subjects.append(StudentSubject(subject_name='数学', total_hours=10, deducted_hours=0, remaining_hours=Decimal(hours)))
            self.db.add(student)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_threshold_requires_comparison(self):
        self.assertIsNone(_detect_threshold('2026年学号123的数学课时'))
        self.assertEqual(_detect_threshold('低于 0 课时'), Decimal('0'))
        self.assertEqual(_detect_threshold('不足 -1.5 课时'), Decimal('-1.5'))
        self.assertIsNone(_detect_threshold('课时不足2026年'))
        self.assertIsNone(_detect_threshold('课时不低于5'))

    def test_compound_filters_and_zero(self):
        result = _execute_parsed_query(self.db, ParsedAiQuery(intent='low_remaining_hours', subject_name='数学', grade='初二', class_type='VIP', threshold=Decimal('0')), False)
        self.assertEqual([x['studentNo'] for x in result.data['students']], ['S1'])
        self.assertEqual(result.data['appliedConditions']['threshold'], 0)

    def test_ambiguous_and_fuzzy_do_not_return_hours(self):
        for name in ('小王', '王'):
            result = _execute_parsed_query(self.db, ParsedAiQuery(intent='subject_hours', student_name=name, subject_name='数学'), False)
            self.assertIn('candidates', result.data)
            self.assertNotIn('remainingHours', result.data)
            self.assertNotIn('13800000001', str(result.data))

    def test_missing_threshold_requests_clarification(self):
        result = _execute_parsed_query(self.db, ParsedAiQuery(intent='low_remaining_hours', subject_name='数学'), False)
        self.assertIn('阈值', result.answer)

if __name__ == '__main__':
    unittest.main()
