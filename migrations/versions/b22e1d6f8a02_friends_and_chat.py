"""friends and chat: friendships, chat threads/messages/reactions/reads/reports

Revision ID: b22e1d6f8a02
Revises: d44a3f8b0c04
Create Date: 2026-11-18 12:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'b22e1d6f8a02'
down_revision: str | None = 'd44a3f8b0c04'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BigId = sa.BigInteger().with_variant(sa.Integer(), 'sqlite')


def upgrade() -> None:
    op.create_table(
        'friendships',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_low', sa.Integer(), nullable=False),
        sa.Column('user_high', sa.Integer(), nullable=False),
        sa.Column('requester_id', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('low_blocks', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('high_blocks', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.CheckConstraint('user_low < user_high', name=op.f('ck_friendships_pair_order')),
        sa.ForeignKeyConstraint(['user_low'], ['users.id'], name=op.f('fk_friendships_user_low_users'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_high'], ['users.id'], name=op.f('fk_friendships_user_high_users'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['requester_id'], ['users.id'], name=op.f('fk_friendships_requester_id_users'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_friendships')),
        sa.UniqueConstraint('user_low', 'user_high', name='uq_friendships_pair'),
    )
    op.create_index(op.f('ix_friendships_user_high'), 'friendships', ['user_high'], unique=False)

    op.create_table(
        'chat_threads',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('room_id', sa.Integer(), nullable=True),
        sa.Column('user_low', sa.Integer(), nullable=True),
        sa.Column('user_high', sa.Integer(), nullable=True),
        sa.Column('notice', sa.String(length=140), nullable=True),
        sa.Column('notice_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['room_id'], ['rooms.id'], name=op.f('fk_chat_threads_room_id_rooms'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_low'], ['users.id'], name=op.f('fk_chat_threads_user_low_users'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_high'], ['users.id'], name=op.f('fk_chat_threads_user_high_users'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_chat_threads')),
        sa.UniqueConstraint('room_id', name=op.f('uq_chat_threads_room_id')),
        sa.UniqueConstraint('user_low', 'user_high', name='uq_chat_threads_pair'),
    )
    op.create_index(op.f('ix_chat_threads_user_high'), 'chat_threads', ['user_high'], unique=False)

    op.create_table(
        'chat_messages',
        sa.Column('id', BigId, nullable=False),
        sa.Column('thread_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('body', sa.String(length=500), nullable=False),
        sa.Column('book_id', sa.Integer(), nullable=True),
        sa.Column('page', sa.Integer(), nullable=True),
        sa.Column('veiled', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('deleted_by', sa.Integer(), nullable=True),
        sa.Column('reply_to_id', BigId, nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['thread_id'], ['chat_threads.id'], name=op.f('fk_chat_messages_thread_id_chat_threads'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_chat_messages_user_id_users'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['book_id'], ['books.id'], name=op.f('fk_chat_messages_book_id_books'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['deleted_by'], ['users.id'], name=op.f('fk_chat_messages_deleted_by_users'), ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['reply_to_id'], ['chat_messages.id'], name=op.f('fk_chat_messages_reply_to_id_chat_messages'), ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_chat_messages')),
    )
    op.create_index('ix_chat_messages_thread_id_id', 'chat_messages', ['thread_id', 'id'], unique=False)
    op.create_index(op.f('ix_chat_messages_user_id'), 'chat_messages', ['user_id'], unique=False)

    op.create_table(
        'chat_reads',
        sa.Column('thread_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('channel', sa.String(length=8), server_default='chat', nullable=False),
        sa.Column('last_read_id', sa.BigInteger(), server_default='0', nullable=False),
        sa.ForeignKeyConstraint(['thread_id'], ['chat_threads.id'], name=op.f('fk_chat_reads_thread_id_chat_threads'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_chat_reads_user_id_users'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('thread_id', 'user_id', 'channel', name=op.f('pk_chat_reads')),
    )
    op.create_index(op.f('ix_chat_reads_user_id'), 'chat_reads', ['user_id'], unique=False)

    op.create_table(
        'chat_reactions',
        sa.Column('message_id', BigId, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('emoji', sa.String(length=16), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['message_id'], ['chat_messages.id'], name=op.f('fk_chat_reactions_message_id_chat_messages'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_chat_reactions_user_id_users'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('message_id', 'user_id', name=op.f('pk_chat_reactions')),
    )
    op.create_index(op.f('ix_chat_reactions_user_id'), 'chat_reactions', ['user_id'], unique=False)

    op.create_table(
        'chat_reports',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('message_id', BigId, nullable=False),
        sa.Column('reporter_id', sa.Integer(), nullable=False),
        sa.Column('reason', sa.String(length=200), nullable=True),
        sa.Column('resolved', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['message_id'], ['chat_messages.id'], name=op.f('fk_chat_reports_message_id_chat_messages'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['reporter_id'], ['users.id'], name=op.f('fk_chat_reports_reporter_id_users'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_chat_reports')),
        sa.UniqueConstraint('message_id', 'reporter_id', name='uq_chat_reports_message_reporter'),
    )
    op.create_index(op.f('ix_chat_reports_reporter_id'), 'chat_reports', ['reporter_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_chat_reports_reporter_id'), table_name='chat_reports')
    op.drop_table('chat_reports')
    op.drop_index(op.f('ix_chat_reactions_user_id'), table_name='chat_reactions')
    op.drop_table('chat_reactions')
    op.drop_index(op.f('ix_chat_reads_user_id'), table_name='chat_reads')
    op.drop_table('chat_reads')
    op.drop_index(op.f('ix_chat_messages_user_id'), table_name='chat_messages')
    op.drop_index('ix_chat_messages_thread_id_id', table_name='chat_messages')
    op.drop_table('chat_messages')
    op.drop_index(op.f('ix_chat_threads_user_high'), table_name='chat_threads')
    op.drop_table('chat_threads')
    op.drop_index(op.f('ix_friendships_user_high'), table_name='friendships')
    op.drop_table('friendships')
