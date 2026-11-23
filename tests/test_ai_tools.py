import unittest
from decimal import Decimal
from pydantic import ValidationError
from test_ai_query_safety import QuerySafetyTests
from app.services.ai_tools import execute_tool
from app.models.student import Student


class ToolTests(QuerySafetyTests):
    def test_search_composition_and_truncation(self):
        result = execute_tool(self.db, 'search_students', {'grade': '初二', 'class_type': 'VIP', 'subject': '数学', 'threshold': 1, 'limit': 1})
        self.assertEqual(result['total'], 2)
        self.assertTrue(result['truncated'])
        self.assertEqual(result['students'][0]['studentNo'], 'S1')

    def test_hours_are_requeried(self):
        first = execute_tool(self.db, 'get_student_hours', {'student_id': 1})
        student = self.db.get(Student, 1)
        student.subjects[0].remaining_hours = Decimal('2')
        self.db.commit()
        second = execute_tool(self.db, 'get_student_hours', {'student_id': 1})
        self.assertEqual(first['subjects'][0]['remainingHours'], -1)
        self.assertEqual(second['subjects'][0]['remainingHours'], 2)

    def test_invalid_tool_and_arguments_rejected(self):
        with self.assertRaises(ValueError):
            execute_tool(self.db, 'delete_student', {'student_id': 1})
        with self.assertRaises(ValidationError):
            execute_tool(self.db, 'get_student_hours', {'student_id': 1, 'sql': 'DELETE'})
        with self.assertRaises(ValueError):
            execute_tool(self.db, 'search_students', {'threshold': 0})
        with self.assertRaises(ValueError):
            execute_tool(self.db, 'get_lesson_records', {'student_id': 999})

if __name__ == '__main__':
    unittest.main()
