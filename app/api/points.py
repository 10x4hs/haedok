from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_api_user
from app.db.session import get_session
from app.models import User
from app.services import points

router = APIRouter(tags=["points"])


async def _me_points(session: AsyncSession, user: User) -> dict:
    s = await points.summary(session, user.id)
    return {
        "total": s.total,
        "week": s.week,
        "streak_days": s.streak,
        "week_start": points.week_start(points.clock()).isoformat(),
        "rules": points.RULES,
        "ledger": await points.ledger(session, user.id),
    }


@router.get("/api/me/points")
async def me_points(
    session: AsyncSession = Depends(get_session), user: User = Depends(require_api_user)
) -> dict:
    """내 포인트: 총합 · 이번 주 · 연속 일수 · 최근 원장 20줄."""
    return await _me_points(session, user)


@router.get("/api/v1/me/points", include_in_schema=False)
async def me_points_v1(
    session: AsyncSession = Depends(get_session), user: User = Depends(require_api_user)
) -> dict:
    """다른 API 와 같은 /api/v1 접두사로도 받는다."""
    return await _me_points(session, user)
