import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models import Admin, Student
from app.scripts.seed_example import create_example


class PublicSeedTests(unittest.TestCase):
    def test_isolated_example_has_generated_credential_and_invented_identifier(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "example.sqlite3"
            password = create_example(target)
            self.assertGreaterEqual(len(password), 24)
            engine = create_engine(f"sqlite:///{target}")
            try:
                with Session(engine) as db:
                    self.assertEqual(db.scalar(select(Admin)).username, "fictional-example-admin")
                    student = db.scalar(select(Student))
                    self.assertEqual(student.phone, "DEMO-PHONE-001")
                    self.assertEqual(student.subjects[0].remaining_hours, 10)
            finally:
                engine.dispose()
            with self.assertRaises(FileExistsError):
                create_example(target)
