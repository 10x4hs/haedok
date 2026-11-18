"""하루 목표 · 잔디 JSON API (feature "goals", 설정 FEATURE_GOALS).

GET  /api/v1/me/goal?weeks=17  — 오늘 쪽수, 목표, 이번 주, 연속·보호권, 잔디
PUT  /api/v1/me/goal {"pages": 3|5|10|20}  — 목표 바꾸기(CSRF 헤더)
기능이 꺼져 있으면 두 경로 모두 404 이고, 화면(/me, 홈)의 목표 칸도 그리지 않는다.
"""

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import check_csrf, require_api_user
from app.core import ratelimit
from app.core.config import get_settings
from app.db.session import get_session
from app.models import User
from app.services import goals
from app.web import templates


def enabled() -> bool:
    return get_settings().feature_goals


# 화면(_goal.html, _goal_chip.html)이 기능 스위치를 본다 — admin.is_admin 과 같은 방식
templates.env.globals["feature_goals"] = enabled


def _require_feature() -> None:
    if not enabled():
        raise HTTPException(404, "Not Found")


router = APIRouter(prefix="/api/v1/me", tags=["goals"], dependencies=[Depends(_require_feature)])

PUT_LIMIT, PUT_WINDOW_S = 30, 600  # 목표 바꾸기는 10분에 30번이면 넉넉하다


class GoalIn(BaseModel):
    pages: int


@router.get("/goal")
async def get_goal(
    weeks: int = Query(goals.WEEKS_PHONE, ge=1, le=goals.WEEKS_MAX),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_api_user),
) -> dict:
    return await goals.status(session, user.id, weeks)


@router.put("/goal")
async def put_goal(
    body: GoalIn,
    request: Request,
    weeks: int = Query(goals.WEEKS_PHONE, ge=1, le=goals.WEEKS_MAX),
    x_csrf_token: str | None = Header(None),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_api_user),
) -> dict:
    check_csrf(request, x_csrf_token)
    try:
        goals.validate_goal(body.pages)
    except ValueError as e:
        raise HTTPException(422, "목표는 3·5·10·20쪽 중에서 골라 주세요") from e
    try:
        await ratelimit.check(
            session,
            f"goal:{user.id}",
            PUT_LIMIT,
            PUT_WINDOW_S,
            "목표를 너무 자주 바꿨어요. 잠시 뒤에 다시 해 주세요",
        )
    except ratelimit.RateLimited as e:
        raise HTTPException(429, e.message, headers={"Retry-After": str(e.retry_after)}) from e
    await goals.set_goal(session, user.id, body.pages)
    return await goals.status(session, user.id, weeks)
