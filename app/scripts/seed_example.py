"""Generate an isolated SQLite example from invented records only.

This script never uses the configured MySQL engine or private seed_data.py.
"""
import secrets
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.core.security import get_password_hash
from app.models import Admin, Student, StudentSubject


def create_example(path: Path) -> str:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite {path}")
    password = secrets.token_urlsafe(24)
    engine = create_engine(f"sqlite:///{path}")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            db.add(Admin(username="fictional-example-admin", password_hash=get_password_hash(password), role="admin"))
            student = Student(name="虚构学员甲", phone="DEMO-PHONE-001", student_no="DEMO-STUDENT-001", grade="初一", class_type="VIP")
            student.subjects = [StudentSubject(subject_name="数学", total_hours=12, deducted_hours=2, remaining_hours=10)]
            db.add(student)
            db.commit()
    finally:
        engine.dispose()
    return password


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m app.scripts.seed_example path/to/new-example.sqlite3")
    target = Path(sys.argv[1]).expanduser().resolve()
    password = create_example(target)
    print("Example SQLite created. Admin: fictional-example-admin")
    print(f"One-time local password: {password}")
