"""Validated, read-only business tools for the administrator assistant."""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models.student import Student
from app.models.student_subject import StudentSubject
from app.models.lesson_record import LessonRecord
from app.services.ai_query_service import _active_student_filter, _json_number
from app.services.class_types import CLASS_TYPES, class_type_db_values


class ToolArgs(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True, allow_inf_nan=False)


class SearchStudentsArgs(ToolArgs):
    name: str = Field(default='', max_length=100)
    phone: str = Field(default='', max_length=20)
    student_no: str = Field(default='', max_length=50)
    grade: str = Field(default='', max_length=50)
    class_type: str = Field(default='', max_length=50)
    subject: str = Field(default='', max_length=100)
    threshold: Decimal | None = None
    limit: int = Field(default=50, ge=1, le=100)


class StudentHoursArgs(ToolArgs):
    student_id: int = Field(gt=0)
    subject: str = Field(default='', max_length=100)


class LessonRecordsArgs(StudentHoursArgs):
    limit: int = Field(default=10, ge=1, le=50)


class SearchRulesArgs(ToolArgs):
    query: str = Field(min_length=1, max_length=500)


class SearchDemoRulesArgs(SearchRulesArgs):
    class_type: str = Field(default='', max_length=80)
    category: str = Field(default='', max_length=80)


def search_students(db: Session, args: SearchStudentsArgs) -> dict[str, Any]:
    if args.threshold is not None and not args.subject:
        raise ValueError('课时阈值需要指定科目')
    if args.class_type and args.class_type not in CLASS_TYPES:
        raise ValueError('不支持的班型')
    stmt = select(Student).where(_active_student_filter())
    if args.name:
        stmt = stmt.where(Student.name.contains(args.name, autoescape=True))
    if args.phone:
        stmt = stmt.where(Student.phone == args.phone)
    if args.student_no:
        stmt = stmt.where(Student.student_no == args.student_no)
    if args.grade:
        stmt = stmt.where(Student.grade == args.grade)
    if args.class_type:
        stmt = stmt.where(Student.class_type.in_(class_type_db_values(args.class_type)))
    if args.subject:
        stmt = stmt.join(StudentSubject).where(StudentSubject.subject_name == args.subject)
        if args.threshold is not None:
            stmt = stmt.where(StudentSubject.remaining_hours < args.threshold)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    students = db.scalars(stmt.order_by(Student.id).limit(args.limit)).all()
    items = [{'studentId': s.id, 'studentName': s.name, 'studentNo': s.student_no,
              'grade': s.grade, 'classType': s.class_type,
              'phone': s.phone[:3] + '****' + s.phone[-4:] if len(s.phone) == 11 else '****'} for s in students]
    return {'students': items, 'total': total, 'truncated': total > len(items),
            'needsConfirmation': bool(args.name and not args.phone and not args.student_no),
            'appliedConditions': args.model_dump(mode='json', exclude={'limit'})}


def _active_student(db: Session, student_id: int) -> Student:
    student = db.scalar(select(Student).options(selectinload(Student.subjects)).where(
        _active_student_filter(), Student.id == student_id))
    if student is None:
        raise ValueError('学员不存在或已删除')
    return student


def get_student_hours(db: Session, args: StudentHoursArgs) -> dict[str, Any]:
    student = _active_student(db, args.student_id)
    subjects = [s for s in student.subjects if not args.subject or s.subject_name == args.subject]
    return {'studentId': student.id, 'queriedAt': datetime.now(timezone.utc).isoformat(),
            'subjects': [{'subjectName': s.subject_name, 'totalHours': _json_number(s.total_hours),
                          'deductedHours': _json_number(s.deducted_hours),
                          'remainingHours': _json_number(s.remaining_hours)} for s in subjects]}


def get_lesson_records(db: Session, args: LessonRecordsArgs) -> dict[str, Any]:
    _active_student(db, args.student_id)
    stmt = select(LessonRecord).where(LessonRecord.student_id == args.student_id)
    if args.subject:
        stmt = stmt.where(LessonRecord.subject_name == args.subject)
    records = db.scalars(stmt.order_by(LessonRecord.record_date.desc(), LessonRecord.id.desc()).limit(args.limit)).all()
    return {'studentId': args.student_id, 'records': [
        {'subjectName': r.subject_name, 'changeType': r.change_type, 'hours': _json_number(r.hours),
         'remainingHours': _json_number(r.remaining_hours) if r.remaining_hours is not None else None,
         'recordDate': r.record_date.isoformat() if r.record_date else None} for r in records]}


def search_demo_rules(db: Session, args: SearchDemoRulesArgs) -> dict[str, Any]:
    import os
    from app.core.config import settings
    url = db.get_bind().url
    if (os.getenv('JINBI_DEMO_RULES_ENABLED') != '1' or settings.app_env.lower() not in ('test', 'testing', 'local')
        or url.drivername.split('+')[0] != 'sqlite' or url.database not in (None, '', ':memory:')):
        return {'status': 'no_approved_rules', 'answer': '没有启用的 DEMO 规则。', 'citations': [], 'matched_rules': [], 'warnings': [], 'retrieval_metadata': {'mode': 'formal'}}
    from app.services.demo_rule_search import search_approved
    return search_approved(db, args.query, class_type=args.class_type, category=args.category)


def search_rules(db: Session, args: SearchRulesArgs) -> dict[str, Any]:
    # Existing knowledge files are explicitly demo-only and must never serve as
    # institutional policy in the normal tool path.
    from app.services.academic_rules import search
    result = search(args.query, role='admin')
    result['sources'] = result['citations']
    result['insufficientEvidence'] = not result['citations']
    return result


TOOLS = {'search_students': (SearchStudentsArgs, search_students),
         'get_student_hours': (StudentHoursArgs, get_student_hours),
         'get_lesson_records': (LessonRecordsArgs, get_lesson_records),
         'search_rules': (SearchRulesArgs, search_rules),
         'search_demo_academic_rules': (SearchDemoRulesArgs, search_demo_rules)}


def execute_tool(db: Session, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name not in TOOLS:
        raise ValueError('未注册的工具')
    schema, handler = TOOLS[name]
    return handler(db, schema.model_validate(arguments))
