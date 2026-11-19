import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

import app.models_ai  # noqa: F401  (벡터 · 책에게 묻기 기록 · 취향 캐시 등록)
import app.models_chat  # noqa: F401  (친구 · 채팅 테이블 등록)
import app.models_goals  # noqa: F401  (reading_goals·reading_day_marks 등록)
import app.models_mileage  # noqa: F401  (마일리지 테이블 등록)
import app.models_posts  # noqa: F401  (읽은 책 게시물 테이블 등록)
from app import models  # noqa: F401  (메타데이터 등록)
from app import models_board as _board_models  # noqa: F401  (recruit_posts · recruit_reports 등록)
from app.core import ratelimit  # noqa: F401  (rate_limits 테이블 등록)
from app.core.config import get_settings
from app.db.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
URL = get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(url=URL, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def _run(connection) -> None:
    def include_object(obj, name, type_, reflected, compare_to) -> bool:
        # Index(...).ddl_if(dialect="postgresql") 같은 한 DB 전용 색인은 다른 DB 에서 견주지 않는다
        only = getattr(getattr(obj, "_ddl_if", None), "dialect", None)
        return only is None or only == connection.dialect.name

    context.configure(connection=connection, target_metadata=target_metadata, include_object=include_object)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(URL)
    async with engine.connect() as conn:
        await conn.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
