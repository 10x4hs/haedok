from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

_url = get_settings().database_url
_is_pg = _url.startswith("postgresql+asyncpg")
# Neon 같은 연결 풀러(pgbouncer, 트랜잭션 모드) 뒤에서는 asyncpg의 prepared statement 캐시가 깨진다
_connect_args = {"statement_cache_size": 0} if _is_pg and "-pooler" in _url else {}

# 서버리스(Vercel) 풀 설정 — 함수 인스턴스마다 풀이 따로 생기므로 작게 잡는다.
#   pool_size 2 + max_overflow 3: 인스턴스 하나가 한 번에 최대 5연결. 동시 요청이 적고 인스턴스가 많은
#     환경에서 Neon 연결 상한(무료 티어 100)을 넘지 않게
#   pool_recycle 300초: 유휴 연결은 프록시·Neon 쪽에서 먼저 끊기므로 5분마다 새로 맺는다
#   pool_pre_ping: 꺼내기 전에 살아 있는지 확인해 끊긴 연결로 첫 쿼리가 실패하지 않게
# SQLite(로컬·테스트·임시 배포)는 풀 인자를 받지 않으므로 pre_ping만 둔다
_pool_args = (
    {"pool_size": 2, "max_overflow": 3, "pool_recycle": 300, "pool_timeout": 10} if _is_pg else {}
)
engine = create_async_engine(_url, pool_pre_ping=True, connect_args=_connect_args, **_pool_args)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
