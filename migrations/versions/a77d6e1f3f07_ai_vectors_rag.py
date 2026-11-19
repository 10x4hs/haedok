"""AI: vector store (book_vectors, chunk_vectors), ask history (ai_questions), taste cache (ai_tastes)

Postgres(Neon): pgvector 확장을 켜고 vec 열을 vector(AI_EMBED_DIM) 으로, book_vectors 에 HNSW 코사인 색인.
SQLite(테스트 · 로컬): vec 는 float32 바이트, 코사인은 파이썬이 계산한다(app/services/vectors.py).
새 테이블만 만든다(지우기 · 이름 바꾸기 없음).

pgvector 가 없는 Postgres 에서는 일부러 크게 실패한다 — 조용히 넘어가면 기능이 반쯤만 켜진다.
Neon 은 pgvector 를 기본으로 제공한다. CI 는 pgvector/pgvector:pg16 이미지를 쓴다.

Revision ID: a77d6e1f3f07
Revises: f66c5b0d2e06
Create Date: 2026-11-19 10:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.config import get_settings
from app.models_ai import Vector

revision: str = 'a77d6e1f3f07'
down_revision: str | None = 'f66c5b0d2e06'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOW = sa.text('(CURRENT_TIMESTAMP)')
DIM = get_settings().ai_embed_dim

NO_PGVECTOR = (
    "pgvector 확장(vector)이 이 Postgres 에 없어요. Neon 은 기본 제공이고, 직접 운영하는 서버라면 "
    "pgvector 를 설치한 뒤(예: 이미지 pgvector/pgvector:pg16) 다시 마이그레이션해 주세요. "
    "The 'vector' extension is not available on this PostgreSQL server."
)


def _pg() -> bool:
    return op.get_context().dialect.name == 'postgresql'


def _require_pgvector() -> None:
    ctx = op.get_context()
    if not ctx.as_sql:  # --sql(오프라인)에서는 물어볼 수 없다 — CREATE EXTENSION 이 대신 실패한다
        ok = op.get_bind().execute(sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'vector'")).first()
        if ok is None:
            raise RuntimeError(NO_PGVECTOR)
    op.execute('CREATE EXTENSION IF NOT EXISTS vector')


def upgrade() -> None:
    if _pg():
        _require_pgvector()

    op.create_table(
        'book_vectors',
        sa.Column('source', sa.String(length=20), nullable=False),
        sa.Column('external_id', sa.String(length=512), nullable=False),
        sa.Column('title', sa.String(length=500), nullable=False),
        sa.Column('content_hash', sa.String(length=16), nullable=False),
        sa.Column('vec', Vector(DIM), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint('source', 'external_id', name=op.f('pk_book_vectors')),
    )
    if _pg():
        op.create_index(
            'ix_book_vectors_vec_hnsw', 'book_vectors', ['vec'], unique=False,
            postgresql_using='hnsw', postgresql_ops={'vec': 'vector_cosine_ops'},
        )

    op.create_table(
        'chunk_vectors',
        sa.Column('book_id', sa.Integer(), nullable=False),
        sa.Column('chunk_index', sa.Integer(), nullable=False),
        sa.Column('part', sa.SmallInteger(), nullable=False),
        sa.Column('vec', Vector(DIM), nullable=False),
        sa.ForeignKeyConstraint(['book_id'], ['books.id'], name=op.f('fk_chunk_vectors_book_id_books'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('book_id', 'chunk_index', 'part', name=op.f('pk_chunk_vectors')),
    )
    # chunk_vectors 는 근사 색인을 두지 않는다: 질문마다 '이 책 · 읽은 쪽까지'로 거른 뒤 정확히 잰다
    # (한 권은 많아야 수천 조각). 근사 색인은 거르기 전에 이웃을 잘라 버려 결과가 비기 쉽다.

    op.create_table(
        'ai_questions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('book_id', sa.Integer(), nullable=False),
        sa.Column('question', sa.String(length=300), nullable=False),
        sa.Column('answer', sa.Text(), nullable=False),
        sa.Column('pages', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.ForeignKeyConstraint(['book_id'], ['books.id'], name=op.f('fk_ai_questions_book_id_books'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_ai_questions_user_id_users'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_ai_questions')),
    )
    op.create_index('ix_ai_questions_user_book', 'ai_questions', ['user_id', 'book_id', 'id'], unique=False)

    op.create_table(
        'ai_tastes',
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('vec', sa.LargeBinary(), nullable=True),
        sa.Column('basis_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('computed_at', sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_ai_tastes_user_id_users'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('user_id', name=op.f('pk_ai_tastes')),
    )


def downgrade() -> None:
    op.drop_table('ai_tastes')
    op.drop_index('ix_ai_questions_user_book', table_name='ai_questions')
    op.drop_table('ai_questions')
    op.drop_table('chunk_vectors')
    if _pg():
        op.drop_index('ix_book_vectors_vec_hnsw', table_name='book_vectors')
    op.drop_table('book_vectors')
    # 확장(vector)은 남겨 둔다 — 다른 곳에서 쓸 수 있고, 지우려면 슈퍼유저 권한이 필요할 수 있다
