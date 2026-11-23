from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


class AiConversation(Base):
    __tablename__ = 'ai_conversations'
    id: Mapped[int] = mapped_column(primary_key=True)
    admin_id: Mapped[int] = mapped_column(ForeignKey('admins.id'), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    context: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    summary: Mapped[str] = mapped_column(Text, default='', nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    messages: Mapped[list['AiMessage']] = relationship(back_populates='conversation', cascade='all, delete-orphan')


class AiMessage(Base):
    __tablename__ = 'ai_messages'
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey('ai_conversations.id', ondelete='CASCADE'), index=True, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    result: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    conversation: Mapped[AiConversation] = relationship(back_populates='messages')


class AiAdminPreference(Base):
    __tablename__ = 'ai_admin_preferences'
    admin_id: Mapped[int] = mapped_column(ForeignKey('admins.id'), primary_key=True)
    grade: Mapped[str] = mapped_column(String(50), default='', nullable=False)
    class_type: Mapped[str] = mapped_column(String(50), default='', nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
