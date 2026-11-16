"""points_log unique (user_id, source, ref_id)

같은 일로 두 번 적립되지 않게 DB 제약으로 막는다. 이전에는 코드가 넣기 전에 찾아보는 방식이라
동시 요청이 겹치면 중복이 생길 수 있었다 — 제약을 걸기 전에 그런 중복을 먼저 지운다(가장 먼저
들어온 행만 남김). ref_id 가 없는 행은 unique 대상이 아니므로 건드리지 않는다.

Revision ID: c2d4e6f8a013
Revises: b7c2d9e4f601
Create Date: 2026-11-16 12:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'c2d4e6f8a013'
down_revision: str | None = 'b7c2d9e4f601'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NAME = 'uq_points_log_user_source_ref'


def upgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM points_log WHERE ref_id IS NOT NULL AND id NOT IN ("
            " SELECT MIN(id) FROM points_log WHERE ref_id IS NOT NULL GROUP BY user_id, source, ref_id)"
        )
    )
    # batch: SQLite 는 ALTER TABLE ADD CONSTRAINT 가 없어 테이블을 다시 만든다(Postgres 는 바로 ALTER)
    with op.batch_alter_table('points_log') as batch:
        batch.create_unique_constraint(NAME, ['user_id', 'source', 'ref_id'])


def downgrade() -> None:
    # 지운 중복 행은 되살리지 않는다(애초에 잘못 들어간 적립이다)
    with op.batch_alter_table('points_log') as batch:
        batch.drop_constraint(NAME, type_='unique')
