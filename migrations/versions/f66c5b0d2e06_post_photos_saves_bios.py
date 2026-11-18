"""post photos, saves, comment likes/replies/reports, profile bios; caption 2,200

인스타그램처럼: 사진 1~5장(post_photos, JPEG 바이트), 저장(post_saves), 댓글 좋아요(post_comment_likes),
답글(post_comments.parent_id), 댓글 신고(post_comment_reports), 프로필 소개(profile_bios),
캡션 500 → 2,200자. 테이블 · 널 허용 열 추가와 varchar 넓히기만 한다(지우기 · 이름 바꾸기 없음).

Revision ID: f66c5b0d2e06
Revises: e55b4a9c1d05
Create Date: 2026-11-18 23:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'f66c5b0d2e06'
down_revision: str | None = 'e55b4a9c1d05'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOW = sa.text('(CURRENT_TIMESTAMP)')


def _sqlite() -> bool:
    return op.get_context().dialect.name == 'sqlite'


def upgrade() -> None:
    op.create_table(
        'post_photos',
        sa.Column('post_id', sa.Integer(), nullable=False),
        sa.Column('idx', sa.SmallInteger(), nullable=False),
        sa.Column('data', sa.LargeBinary(), nullable=False),
        sa.Column('width', sa.Integer(), nullable=False),
        sa.Column('height', sa.Integer(), nullable=False),
        sa.Column('digest', sa.String(length=16), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.ForeignKeyConstraint(['post_id'], ['reading_posts.id'], name=op.f('fk_post_photos_post_id_reading_posts'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('post_id', 'idx', name=op.f('pk_post_photos')),
    )

    op.create_table(
        'post_saves',
        sa.Column('post_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.ForeignKeyConstraint(['post_id'], ['reading_posts.id'], name=op.f('fk_post_saves_post_id_reading_posts'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_post_saves_user_id_users'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('post_id', 'user_id', name=op.f('pk_post_saves')),
    )
    op.create_index('ix_post_saves_user_id_created_at', 'post_saves', ['user_id', 'created_at'], unique=False)

    # 답글: 윗댓글 id. batch — SQLite 는 ALTER 로 외래 키를 못 붙여 테이블을 다시 만든다(Postgres 는 바로 ALTER)
    with op.batch_alter_table('post_comments') as batch:
        batch.add_column(sa.Column('parent_id', sa.Integer(), nullable=True))
        batch.create_foreign_key(
            op.f('fk_post_comments_parent_id_post_comments'), 'post_comments', ['parent_id'], ['id'], ondelete='CASCADE'
        )
        batch.create_index('ix_post_comments_parent_id', ['parent_id'], unique=False)

    op.create_table(
        'post_comment_likes',
        sa.Column('comment_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.ForeignKeyConstraint(['comment_id'], ['post_comments.id'], name=op.f('fk_post_comment_likes_comment_id_post_comments'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_post_comment_likes_user_id_users'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('comment_id', 'user_id', name=op.f('pk_post_comment_likes')),
    )
    op.create_index('ix_post_comment_likes_user_id', 'post_comment_likes', ['user_id'], unique=False)

    op.create_table(
        'post_comment_reports',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('comment_id', sa.Integer(), nullable=False),
        sa.Column('reporter_id', sa.Integer(), nullable=False),
        sa.Column('reason', sa.String(length=20), nullable=False),
        sa.Column('resolved', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.ForeignKeyConstraint(['comment_id'], ['post_comments.id'], name=op.f('fk_post_comment_reports_comment_id_post_comments'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['reporter_id'], ['users.id'], name=op.f('fk_post_comment_reports_reporter_id_users'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_post_comment_reports')),
        sa.UniqueConstraint('comment_id', 'reporter_id', name='uq_post_comment_reports_comment_reporter'),
    )
    op.create_index('ix_post_comment_reports_comment_id', 'post_comment_reports', ['comment_id'], unique=False)

    op.create_table(
        'profile_bios',
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('bio', sa.String(length=150), server_default='', nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_profile_bios_user_id_users'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('user_id', name=op.f('pk_profile_bios')),
    )

    # 캡션 500 → 2,200자. varchar 넓히기는 Postgres 에서 데이터를 다시 쓰지 않는다.
    # SQLite 는 길이를 강제하지 않으므로 건드리지 않는다(테이블을 다시 만들 이유가 없다).
    if not _sqlite():
        op.alter_column('reading_posts', 'caption', type_=sa.String(length=2200), existing_type=sa.String(length=500),
                        existing_nullable=False, existing_server_default='')


def downgrade() -> None:
    if not _sqlite():
        # 500자를 넘는 캡션이 있으면 줄일 수 없다 — 그때는 내려가기 전에 직접 확인해야 한다
        op.alter_column('reading_posts', 'caption', type_=sa.String(length=500), existing_type=sa.String(length=2200),
                        existing_nullable=False, existing_server_default='')
    op.drop_table('profile_bios')
    op.drop_index('ix_post_comment_reports_comment_id', table_name='post_comment_reports')
    op.drop_table('post_comment_reports')
    op.drop_index('ix_post_comment_likes_user_id', table_name='post_comment_likes')
    op.drop_table('post_comment_likes')
    with op.batch_alter_table('post_comments') as batch:
        batch.drop_index('ix_post_comments_parent_id')
        batch.drop_constraint(op.f('fk_post_comments_parent_id_post_comments'), type_='foreignkey')
        batch.drop_column('parent_id')
    op.drop_index('ix_post_saves_user_id_created_at', table_name='post_saves')
    op.drop_table('post_saves')
    op.drop_table('post_photos')
