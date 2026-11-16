from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import check_csrf, require_api_user
from app.core import appearance
from app.db.session import get_session
from app.models import User

router = APIRouter(prefix="/api/v1/me", tags=["me"])


class SettingsIn(BaseModel):
    mode: str | None = None
    bg: str | None = None
    accent: str | None = None
    # 테마 id. ""이면 테마를 끄고 직접 고른 값(mode·bg·accent)을 쓴다
    theme: str | None = None


@router.put("/settings")
async def put_settings(
    body: SettingsIn,
    request: Request,
    x_csrf_token: str | None = Header(None),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_api_user),
) -> dict:
    """화면 테마를 계정에 저장한다 — 모르는 값은 기본값으로 바뀐다."""
    check_csrf(request, x_csrf_token)
    merged = (user.settings or {}) | body.model_dump(exclude_none=True)
    look = appearance.resolve(merged)
    user.settings = appearance.saved(look)
    await session.commit()
    return look
