"""reading posts: reading_posts, post_likes, post_comments, post_reports

Instagram처럼 읽은 책을 게시물로 올린다(/posts/new · /p/{id} · /u/{id} · /feed). 테이블 추가만 한다.

Revision ID: e55b4a9c1d05
Revises: b22e1d6f8a02
Create Date: 2026-11-18 18:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'e55b4a9c1d05'
down_revision: str | None = 'b22e1d6f8a02'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOW = sa.text('(CURRENT_TIMESTAMP)')


def upgrade() -> None:
    op.create_table(
        'reading_posts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('author_id', sa.Integer(), nullable=False),
        sa.Column('book_id', sa.Integer(), nullable=True),
        sa.Column('book_source', sa.String(length=20), nullable=False),
        sa.Column('external_id', sa.String(length=512), nullable=False),
        sa.Column('book_title', sa.String(length=500), nullable=False),
        sa.Column('book_author', sa.String(length=300), nullable=True),
        sa.Column('cover_url', sa.String(length=1000), nullable=True),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('rating', sa.SmallInteger(), nullable=True),
        sa.Column('caption', sa.String(length=500), server_default='', nullable=False),
        sa.Column('quote', sa.String(length=200), nullable=True),
        sa.Column('visibility', sa.String(length=10), server_default='all', nullable=False),
        sa.Column('post_day', sa.String(length=10), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('deleted_by', sa.Integer(), nullable=True),
        sa.CheckConstraint('rating IS NULL OR rating BETWEEN 1 AND 5', name=op.f('ck_reading_posts_rating_range')),
        sa.ForeignKeyConstraint(['author_id'], ['users.id'], name=op.f('fk_reading_posts_author_id_users'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['book_id'], ['books.id'], name=op.f('fk_reading_posts_book_id_books'), ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['deleted_by'], ['users.id'], name=op.f('fk_reading_posts_deleted_by_users'), ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_reading_posts')),
        sa.UniqueConstraint('author_id', 'book_source', 'external_id', 'post_day', name='uq_reading_posts_daily'),
    )
    op.create_index('ix_reading_posts_author_id_id', 'reading_posts', ['author_id', 'id'], unique=False)
    op.create_index('ix_reading_posts_visibility_id', 'reading_posts', ['visibility', 'id'], unique=False)

    op.create_table(
        'post_likes',
        sa.Column('post_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.ForeignKeyConstraint(['post_id'], ['reading_posts.id'], name=op.f('fk_post_likes_post_id_reading_posts'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_post_likes_user_id_users'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('post_id', 'user_id', name=op.f('pk_post_likes')),
    )
    op.create_index('ix_post_likes_user_id', 'post_likes', ['user_id'], unique=False)

    op.create_table(
        'post_comments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('post_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('body', sa.String(length=300), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['post_id'], ['reading_posts.id'], name=op.f('fk_post_comments_post_id_reading_posts'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_post_comments_user_id_users'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_post_comments')),
    )
    op.create_index('ix_post_comments_post_id_id', 'post_comments', ['post_id', 'id'], unique=False)
    op.create_index('ix_post_comments_user_id', 'post_comments', ['user_id'], unique=False)

    op.create_table(
        'post_reports',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('post_id', sa.Integer(), nullable=False),
        sa.Column('reporter_id', sa.Integer(), nullable=False),
        sa.Column('reason', sa.String(length=20), nullable=False),
        sa.Column('resolved', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.ForeignKeyConstraint(['post_id'], ['reading_posts.id'], name=op.f('fk_post_reports_post_id_reading_posts'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['reporter_id'], ['users.id'], name=op.f('fk_post_reports_reporter_id_users'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_post_reports')),
        sa.UniqueConstraint('post_id', 'reporter_id', name='uq_post_reports_post_reporter'),
    )
    op.create_index('ix_post_reports_post_id', 'post_reports', ['post_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_post_reports_post_id', table_name='post_reports')
    op.drop_table('post_reports')
    op.drop_index('ix_post_comments_user_id', table_name='post_comments')
    op.drop_index('ix_post_comments_post_id_id', table_name='post_comments')
    op.drop_table('post_comments')
    op.drop_index('ix_post_likes_user_id', table_name='post_likes')
    op.drop_table('post_likes')
    op.drop_index('ix_reading_posts_visibility_id', table_name='reading_posts')
    op.drop_index('ix_reading_posts_author_id_id', table_name='reading_posts')
    op.drop_table('reading_posts')
