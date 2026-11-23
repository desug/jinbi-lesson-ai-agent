"""Add isolated administrator assistant memory."""
from alembic import op
import sqlalchemy as sa

revision = '20261029_ai_memory'
down_revision = '20260503_add_student_school_class_types'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('ai_conversations', sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('admin_id', sa.Integer(), sa.ForeignKey('admins.id'), nullable=False),
        sa.Column('title', sa.String(200), nullable=False), sa.Column('context', sa.JSON(), nullable=False),
        sa.Column('summary', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False))
    op.create_index('ix_ai_conversations_admin_id', 'ai_conversations', ['admin_id'])
    op.create_table('ai_messages', sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('conversation_id', sa.Integer(), sa.ForeignKey('ai_conversations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('role', sa.String(20), nullable=False), sa.Column('content', sa.Text(), nullable=False),
        sa.Column('result', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False))
    op.create_index('ix_ai_messages_conversation_id', 'ai_messages', ['conversation_id'])
    op.create_table('ai_admin_preferences', sa.Column('admin_id', sa.Integer(), sa.ForeignKey('admins.id'), primary_key=True),
        sa.Column('grade', sa.String(50), nullable=False), sa.Column('class_type', sa.String(50), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False))


def downgrade():
    op.drop_table('ai_admin_preferences')
    op.drop_index('ix_ai_messages_conversation_id', table_name='ai_messages')
    op.drop_table('ai_messages')
    op.drop_index('ix_ai_conversations_admin_id', table_name='ai_conversations')
    op.drop_table('ai_conversations')
