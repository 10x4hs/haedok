"""recruit board: recruit_posts, recruit_reports

같이 읽을 사람 모집 게시판(/together). 글 하나가 대결방(rooms) 하나에 붙는다. 테이블 추가만 한다.

Revision ID: c33f2e7a9b03
Revises: c2d4e6f8a013
Create Date: 2026-11-18 12:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'c33f2e7a9b03'
down_revision: str | None = 'c2d4e6f8a013'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'recruit_posts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('room_id', sa.Integer(), nullable=False),
        sa.Column('author_id', sa.Integer(), nullable=False),
        sa.Column('book_id', sa.Integer(), nullable=False),
        sa.Column('pitch', sa.String(length=140), nullable=False),
        sa.Column('pace', sa.SmallInteger(), nullable=False),
        sa.Column('capacity', sa.SmallInteger(), nullable=False),
        sa.Column('seats', sa.Integer(), server_default='1', nullable=False),
        sa.Column('status', sa.String(length=10), server_default='open', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint('capacity BETWEEN 2 AND 8', name=op.f('ck_recruit_posts_capacity')),
        sa.ForeignKeyConstraint(['room_id'], ['rooms.id'], name=op.f('fk_recruit_posts_room_id_rooms'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['author_id'], ['users.id'], name=op.f('fk_recruit_posts_author_id_users'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['book_id'], ['books.id'], name=op.f('fk_recruit_posts_book_id_books'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_recruit_posts')),
        sa.UniqueConstraint('room_id', name=op.f('uq_recruit_posts_room_id')),
    )
    op.create_index('ix_recruit_posts_author_id', 'recruit_posts', ['author_id'], unique=False)
    op.create_index('ix_recruit_posts_status_id', 'recruit_posts', ['status', 'id'], unique=False)
    op.create_table(
        'recruit_reports',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('post_id', sa.Integer(), nullable=False),
        sa.Column('reporter_id', sa.Integer(), nullable=False),
        sa.Column('reason', sa.String(length=20), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['post_id'], ['recruit_posts.id'], name=op.f('fk_recruit_reports_post_id_recruit_posts'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['reporter_id'], ['users.id'], name=op.f('fk_recruit_reports_reporter_id_users'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_recruit_reports')),
        sa.UniqueConstraint('post_id', 'reporter_id', name=op.f('uq_recruit_reports_post_id')),
    )
    op.create_index('ix_recruit_reports_post_id', 'recruit_reports', ['post_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_recruit_reports_post_id', table_name='recruit_reports')
    op.drop_table('recruit_reports')
    op.drop_index('ix_recruit_posts_status_id', table_name='recruit_posts')
    op.drop_index('ix_recruit_posts_author_id', table_name='recruit_posts')
    op.drop_table('recruit_posts')
