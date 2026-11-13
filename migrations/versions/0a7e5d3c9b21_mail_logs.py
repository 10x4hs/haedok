"""mail send log

Revision ID: 0a7e5d3c9b21
Revises: f1d4c8a2b907
Create Date: 2026-11-13 12:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0a7e5d3c9b21'
down_revision: str | None = 'f1d4c8a2b907'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'mail_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('to_email', sa.String(length=255), nullable=False),
        sa.Column('kind', sa.String(length=40), nullable=False),
        sa.Column('ok', sa.Boolean(), nullable=False),
        sa.Column('transport', sa.String(length=20), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('duration_ms', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_mail_logs')),
    )
    op.create_index(op.f('ix_mail_logs_to_email'), 'mail_logs', ['to_email'], unique=False)
    op.create_index(op.f('ix_mail_logs_created_at'), 'mail_logs', ['created_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_mail_logs_created_at'), table_name='mail_logs')
    op.drop_index(op.f('ix_mail_logs_to_email'), table_name='mail_logs')
    op.drop_table('mail_logs')
