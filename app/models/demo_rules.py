"""Isolated DEMO-only rule approval records."""
from datetime import datetime
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class DemoRulePermission(Base):
    __tablename__ = 'demo_rule_permissions'
    admin_id: Mapped[int] = mapped_column(ForeignKey('admins.id'), primary_key=True)
    can_approve_rules: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class DemoRule(Base):
    __tablename__ = 'demo_rules'
    __table_args__ = (UniqueConstraint('document_id', 'version'),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    class_type: Mapped[str] = mapped_column(String(80), nullable=False)
    effective_date: Mapped[str] = mapped_column(String(10), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    supersedes_id: Mapped[int | None] = mapped_column(ForeignKey('demo_rules.id'))
    state: Mapped[str] = mapped_column(String(20), nullable=False, default='draft')
    owner_id: Mapped[int] = mapped_column(ForeignKey('admins.id'), nullable=False)


class DemoRuleEvent(Base):
    __tablename__ = 'demo_rule_events'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey('demo_rules.id'), nullable=False)
    admin_id: Mapped[int] = mapped_column(ForeignKey('admins.id'), nullable=False)
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
