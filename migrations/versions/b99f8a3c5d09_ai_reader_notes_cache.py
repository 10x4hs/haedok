"""AI in the reader: ai_notes (AI 노트 on a paragraph) and ai_book_cache (지난 이야기 · 등장인물 · 질문)

새 테이블만 만든다. Postgres · SQLite 둘 다 같은 SQL.

Revision ID: b99f8a3c5d09
Revises: a77d6e1f3f07
Create Date: 2026-11-19 15:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'b99f8a3c5d09'
down_revision: str | None = 'a77d6e1f3f07'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOW = sa.text('(CURRENT_TIMESTAMP)')


def upgrade() -> None:
    op.create_table(
        'ai_notes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('book_id', sa.Integer(), nullable=False),
        sa.Column('chunk_index', sa.Integer(), nullable=False),
        sa.Column('anchor', sa.SmallInteger(), nullable=False),
        sa.Column('kind', sa.String(length=12), nullable=False),
        sa.Column('quote', sa.String(length=300), nullable=False),
        sa.Column('answer', sa.Text(), nullable=False),
        sa.Column('pages', sa.JSON(), nullable=False),
        sa.Column('saved', sa.Boolean(), nullable=False),
        sa.Column('reaction_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.ForeignKeyConstraint(['book_id'], ['books.id'], name=op.f('fk_ai_notes_book_id_books'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['reaction_id'], ['reactions.id'], name=op.f('fk_ai_notes_reaction_id_reactions'), ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_ai_notes_user_id_users'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_ai_notes')),
    )
    op.create_index('ix_ai_notes_user_book', 'ai_notes', ['user_id', 'book_id', 'chunk_index'], unique=False)
    op.create_table(
        'ai_book_cache',
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('book_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=10), nullable=False),
        sa.Column('verified_chunk', sa.Integer(), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.ForeignKeyConstraint(['book_id'], ['books.id'], name=op.f('fk_ai_book_cache_book_id_books'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_ai_book_cache_user_id_users'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('user_id', 'book_id', 'kind', name=op.f('pk_ai_book_cache')),
    )


def downgrade() -> None:
    op.drop_table('ai_book_cache')
    op.drop_index('ix_ai_notes_user_book', table_name='ai_notes')
    op.drop_table('ai_notes')
