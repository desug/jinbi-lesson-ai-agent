import unittest
from app.services.demo_natural_input import require_fictional_query

class FictionalInputTests(unittest.TestCase):
    def test_requested_fictional_queries_are_preserved(self):
        for q in ('帮我看看虚构小明数学课还有没有剩余。','查一下虚构小明还剩多少课。'):
            with self.subTest(query=q): self.assertEqual(require_fictional_query(q),q)
    def test_unknown_and_sensitive_input_fails_closed(self):
        # Synthetic markers are never included in error messages or test logs.
        cases=('Bearer abc.def.ghi','eyJhbGciOi.fake.signature','sk-fake-secret',
               '虚构小明电话13800000001','查询其他学生数学课时','', '课'*201)
        for q in cases:
            try: require_fictional_query(q)
            except ValueError as exc:
                self.assertEqual(str(exc),'输入超出虚构演示允许范围')
                if q: self.assertNotIn(q,str(exc))
            else: self.fail('sensitive or unknown input was accepted')
