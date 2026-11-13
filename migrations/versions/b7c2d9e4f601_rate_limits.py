"""rate limits (DB fixed window)

Revision ID: b7c2d9e4f601
Revises: 0a7e5d3c9b21
Create Date: 2026-11-13 18:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'b7c2d9e4f601'
down_revision: str | None = '0a7e5d3c9b21'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'rate_limits',
        sa.Column('key', sa.String(length=200), nullable=False),
        sa.Column('window_start', sa.DateTime(timezone=True), nullable=False),
        sa.Column('count', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('key', name=op.f('pk_rate_limits')),
    )


def downgrade() -> None:
    op.drop_table('rate_limits')
