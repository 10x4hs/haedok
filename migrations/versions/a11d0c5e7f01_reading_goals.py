"""reading goals, streak freezes and repairs (feature goals)

reading_goals: 사람마다 하루 목표 쪽수(3/5/10/20, 기본 5), 보호권 개수(최대 2), 정산한 마지막 읽기 날짜.
reading_day_marks: 읽지 않았지만 연속이 이어진 날 — 보호권(freeze) 또는 무료 복구(repair). (user, day) unique.
새 테이블만 만든다(기존 테이블은 건드리지 않는다).

Revision ID: a11d0c5e7f01
Revises: c2d4e6f8a013
Create Date: 2026-11-18 12:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'a11d0c5e7f01'
down_revision: str | None = 'c2d4e6f8a013'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'reading_goals',
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('daily_pages', sa.SmallInteger(), nullable=False, server_default='5'),
        sa.Column('freezes', sa.SmallInteger(), nullable=False, server_default='0'),
        sa.Column('settled_through', sa.Date(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ['user_id'], ['users.id'], name=op.f('fk_reading_goals_user_id_users'), ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('user_id', name=op.f('pk_reading_goals')),
    )
    op.create_table(
        'reading_day_marks',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('day', sa.Date(), nullable=False),
        sa.Column('kind', sa.String(length=10), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ['user_id'], ['users.id'], name=op.f('fk_reading_day_marks_user_id_users'), ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_reading_day_marks')),
        sa.UniqueConstraint('user_id', 'day', name='uq_reading_day_marks_user_day'),
    )


def downgrade() -> None:
    op.drop_table('reading_day_marks')
    op.drop_table('reading_goals')
