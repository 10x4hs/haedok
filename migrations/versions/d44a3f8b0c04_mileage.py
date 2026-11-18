"""mileage: spend ledger, reward items, reward redemptions (+ sample catalogue)

추가만 한다: 새 표 셋과 예시 상품 세 줄. 예시 상품은 모두 지어낸 이름이고 is_sample=true 라
교환해도 마일리지가 빠지지 않는다(app/services/mileage.py SAMPLE_ITEMS 와 같은 목록).

Revision ID: d44a3f8b0c04
Revises: c33f2e7a9b03
Create Date: 2026-11-18 12:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'd44a3f8b0c04'
down_revision: str | None = 'c33f2e7a9b03'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PENDING = sa.text("status = 'requested'")


def upgrade() -> None:
    op.create_table(
        'mileage_spends',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('amount', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('ref', sa.String(length=64), nullable=True),
        sa.Column('idem_key', sa.String(length=64), nullable=True),
        sa.Column('seq', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_mileage_spends_user_id_users'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_mileage_spends')),
        sa.UniqueConstraint('user_id', 'seq', name='uq_mileage_spends_user_seq'),
        sa.UniqueConstraint('user_id', 'idem_key', name='uq_mileage_spends_user_idem'),
    )
    op.create_index('ix_mileage_spends_user_id', 'mileage_spends', ['user_id'], unique=False)

    items = op.create_table(
        'reward_items',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('partner_name', sa.String(length=60), nullable=False),
        sa.Column('title', sa.String(length=100), nullable=False),
        sa.Column('cost', sa.Integer(), nullable=False),
        sa.Column('stock', sa.Integer(), nullable=True),
        sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('is_sample', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('sort', sa.Integer(), server_default='0', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_reward_items')),
    )

    op.create_table(
        'reward_redemptions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('item_id', sa.Integer(), nullable=False),
        sa.Column('cost', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), server_default='requested', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('handled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('admin_note', sa.String(length=280), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_reward_redemptions_user_id_users'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['item_id'], ['reward_items.id'], name=op.f('fk_reward_redemptions_item_id_reward_items')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_reward_redemptions')),
    )
    op.create_index('ix_reward_redemptions_user_id', 'reward_redemptions', ['user_id'], unique=False)
    op.create_index('ix_reward_redemptions_status_id', 'reward_redemptions', ['status', 'id'], unique=False)
    op.create_index(
        'uq_reward_redemptions_one_pending', 'reward_redemptions', ['user_id'], unique=True,
        sqlite_where=PENDING, postgresql_where=PENDING,
    )

    op.bulk_insert(
        items,
        [
            {'partner_name': '동네 카페(예시)', 'title': '따뜻한 음료 한 잔', 'cost': 300, 'stock': None,
             'active': True, 'is_sample': True, 'sort': 10},
            {'partner_name': '골목 책방(예시)', 'title': '책 5,000원 할인권', 'cost': 500, 'stock': None,
             'active': True, 'is_sample': True, 'sort': 20},
            {'partner_name': '모퉁이 문구점(예시)', 'title': '연필과 책갈피 세트', 'cost': 200, 'stock': None,
             'active': True, 'is_sample': True, 'sort': 30},
        ],
    )


def downgrade() -> None:
    op.drop_index('uq_reward_redemptions_one_pending', table_name='reward_redemptions')
    op.drop_index('ix_reward_redemptions_status_id', table_name='reward_redemptions')
    op.drop_index('ix_reward_redemptions_user_id', table_name='reward_redemptions')
    op.drop_table('reward_redemptions')
    op.drop_table('reward_items')
    op.drop_index('ix_mileage_spends_user_id', table_name='mileage_spends')
    op.drop_table('mileage_spends')
