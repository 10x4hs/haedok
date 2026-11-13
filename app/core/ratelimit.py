"""DB 고정창(fixed window) 속도 제한 — 서버리스에서도 인스턴스 사이에 공유된다.

rate_limits(key PK, window_start, count) 한 행이 (키, 현재 창)의 횟수다.
INSERT … ON CONFLICT DO UPDATE 한 문장으로 올리므로 동시에 눌러도 횟수가 새지 않는다.
SQLite(로컬·테스트)와 Postgres(운영) 둘 다 같은 문법을 지원한다.
"""

import time
from datetime import UTC, datetime

from sqlalchemy import Column, DateTime, Integer, String, Table, case, select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Base

rate_limits = Table(
    "rate_limits",
    Base.metadata,
    Column("key", String(200), primary_key=True),
    Column("window_start", DateTime(timezone=True), nullable=False),
    Column("count", Integer, nullable=False),
)


class RateLimited(Exception):
    """한도를 넘었다. message는 화면의 폼 오류 자리에 그대로 보여 준다."""

    def __init__(self, message: str, retry_after: int) -> None:
        super().__init__(message)
        self.message, self.retry_after = message, retry_after


def _now() -> float:  # 테스트에서 바꿔 창을 넘긴다
    return time.time()


async def hit(session: AsyncSession, key: str, limit: int, window_s: int) -> int:
    """key의 현재 창 횟수를 1 올리고 돌려준다. 호출한 쪽이 limit과 비교한다."""
    now = _now()
    start = datetime.fromtimestamp(now - (now % window_s), UTC)
    dialect = session.bind.dialect.name if session.bind is not None else "sqlite"
    insert = postgresql.insert if dialect == "postgresql" else sqlite.insert
    stmt = insert(rate_limits).values(key=key, window_start=start, count=1)
    stmt = stmt.on_conflict_do_update(
        index_elements=[rate_limits.c.key],
        set_={
            "count": case(
                (rate_limits.c.window_start == stmt.excluded.window_start, rate_limits.c.count + 1), else_=1
            ),
            "window_start": stmt.excluded.window_start,
        },
    )
    await session.execute(stmt)
    count = await session.scalar(select(rate_limits.c.count).where(rate_limits.c.key == key))
    await session.commit()
    return int(count or 0)


async def check(session: AsyncSession, key: str, limit: int, window_s: int, message: str) -> None:
    """한도를 넘으면 RateLimited. 넘지 않으면 조용히 지나간다."""
    count = await hit(session, key, limit, window_s)
    if count > limit:
        now = _now()
        raise RateLimited(message, retry_after=int(window_s - (now % window_s)) or 1)


def client_ip(request) -> str:
    """Vercel·프록시 뒤에서는 x-forwarded-for의 첫 주소가 실제 클라이언트다."""
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() or (request.client.host if request.client else "")


# --- 적용 규칙 (10분 창) ---
WINDOW_S = 600
LOGIN_LIMIT = 10  # 이메일+IP
SIGNUP_EMAIL_LIMIT = 5
SIGNUP_IP_LIMIT = 20
JOIN_LIMIT = 30  # 사용자

LOGIN_MSG = "로그인을 너무 여러 번 시도했어요. 10분 뒤에 다시 해 주세요"
SIGNUP_MSG = "인증 메일을 너무 자주 요청했어요. 10분 뒤에 다시 해 주세요"
JOIN_MSG = "대결 참가를 너무 자주 시도했어요. 10분 뒤에 다시 해 주세요"


async def check_login(session: AsyncSession, email: str, ip: str) -> None:
    await check(session, f"login:{email.strip().lower()}|{ip}", LOGIN_LIMIT, WINDOW_S, LOGIN_MSG)


async def check_signup(session: AsyncSession, email: str, ip: str) -> None:
    """가입·재발송 공통: 이메일별 5번, IP별 20번."""
    await check(session, f"signup-ip:{ip}", SIGNUP_IP_LIMIT, WINDOW_S, SIGNUP_MSG)
    await check(session, f"signup:{email.strip().lower()}", SIGNUP_EMAIL_LIMIT, WINDOW_S, SIGNUP_MSG)


async def check_join(session: AsyncSession, user_id: int) -> None:
    await check(session, f"join:{user_id}", JOIN_LIMIT, WINDOW_S, JOIN_MSG)
