"""채팅 JSON API — 대화 화면(app/static/chat.js)이 3초마다 폴링한다.

GET    /api/v1/chat/{room|dm}/{key}/messages
       ?after=<id> 새 메시지 · ?before=<id> 지난 메시지 · 없으면 최근 50개
       &locked=1,2 잠긴 채 들고 있는 메시지(따라잡았으면 본문이 온다)
       &deleted_since=<now> 그 뒤에 지워진 메시지
POST   /api/v1/chat/{room|dm}/{key}/messages   {"text", "channel": "book"|"chat", "reply_to"}
PUT    /api/v1/chat/messages/{id}/reaction       {"emoji"} · DELETE 로 빼기
PUT    /api/v1/chat/room/{id}/notice              {"text"} 방장만
DELETE /api/v1/chat/messages/{id}               내 메시지 · 방장은 방 메시지
POST   /api/v1/chat/messages/{id}/report        {"reason"}

모든 요청은 로그인 + (쓰기는) CSRF, 대화마다 방 참가 · 친구 수락을 매번 확인한다. 아니면 404.
"""

import datetime as dt

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import check_csrf, require_api_user
from app.core import ratelimit
from app.core.config import get_settings
from app.db.session import get_session
from app.models import User
from app.services import chat


def require_chat() -> None:
    """FEATURE_CHAT 이 꺼져 있으면 있는지조차 알리지 않는다."""
    if not get_settings().feature_chat:
        raise HTTPException(404)


router = APIRouter(prefix="/api/v1/chat", tags=["chat"], dependencies=[Depends(require_chat)])


class MessageIn(BaseModel):
    """쪽(page) 같은 값은 받지 않는다 — 도장은 채널이 정하고 서버가 검증 기록에서 찍는다.

    모르는 필드는 버린다.
    """

    text: str = ""
    channel: str | None = None  # 방 채팅: book('# 책 이야기' · 쪽 도장) | chat('# 수다')
    mode: str | None = None  # 예전 이름(book | chat) — channel 이 없을 때만 본다
    reply_to: int | None = None

    def channel_name(self) -> str:
        return self.channel or ("chat" if self.mode == "chat" else "book")


class ReactionIn(BaseModel):
    emoji: str


class NoticeIn(BaseModel):
    text: str = ""


class ReportIn(BaseModel):
    reason: str | None = None


async def _conv(session: AsyncSession, user: User, kind: str, key: int) -> chat.Conv:
    try:
        if kind == "room":
            return await chat.room_conv(session, user, key)
        if kind == "dm":
            return await chat.dm_conv(session, user, key)
    except chat.NotFound as e:
        raise HTTPException(404, "conversation not found") from e
    raise HTTPException(404, "conversation not found")


def _ids(raw: str | None) -> list[int]:
    out = []
    for part in (raw or "").split(",")[: chat.LOCKED_MAX]:
        if part.strip().isdigit():
            out.append(int(part))
    return out


def _since(raw: str | None) -> dt.datetime | None:
    if not raw:
        return None
    try:
        t = dt.datetime.fromisoformat(raw)
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.UTC)


def _limited(e: ratelimit.RateLimited) -> JSONResponse:
    return JSONResponse({"detail": e.message}, status_code=429, headers={"Retry-After": str(e.retry_after)})


@router.get("/{kind}/{key}/messages")
async def list_messages(
    kind: str,
    key: int,
    after: int | None = None,
    before: int | None = None,
    locked: str | None = None,
    deleted_since: str | None = None,
    channel: str | None = None,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_api_user),
) -> dict:
    conv = await _conv(session, user, kind, key)
    return await chat.messages(
        session,
        user,
        conv,
        after=after,
        before=before,
        locked=_ids(locked),
        deleted_since=_since(deleted_since),
        channel=channel,
    )


@router.post("/{kind}/{key}/messages", status_code=201)
async def send_message(
    kind: str,
    key: int,
    body: MessageIn,
    request: Request,
    x_csrf_token: str | None = Header(None),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_api_user),
):
    check_csrf(request, x_csrf_token)
    conv = await _conv(session, user, kind, key)
    try:
        return await chat.send(
            session, user, conv, body.text, channel=body.channel_name(), reply_to=body.reply_to
        )
    except chat.ChatError as e:
        raise HTTPException(e.status, e.message) from e
    except ratelimit.RateLimited as e:
        return _limited(e)


@router.delete("/messages/{message_id}", status_code=204)
async def delete_message(
    message_id: int,
    request: Request,
    x_csrf_token: str | None = Header(None),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_api_user),
) -> None:
    check_csrf(request, x_csrf_token)
    try:
        await chat.remove(session, user, message_id)
    except chat.NotFound as e:
        raise HTTPException(404, "message not found") from e


@router.post("/messages/{message_id}/report", status_code=201)
async def report_message(
    message_id: int,
    body: ReportIn,
    request: Request,
    x_csrf_token: str | None = Header(None),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_api_user),
):
    check_csrf(request, x_csrf_token)
    try:
        await chat.report(session, user, message_id, body.reason)
    except chat.NotFound as e:
        raise HTTPException(404, "message not found") from e
    except chat.ChatError as e:
        raise HTTPException(e.status, e.message) from e
    except ratelimit.RateLimited as e:
        return _limited(e)
    return {"ok": True}


@router.put("/messages/{message_id}/reaction")
async def put_reaction(
    message_id: int,
    body: ReactionIn,
    request: Request,
    x_csrf_token: str | None = Header(None),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_api_user),
):
    """반응 달기 · 바꾸기 — 한 사람이 한 메시지에 하나."""
    check_csrf(request, x_csrf_token)
    return await _react(session, user, message_id, body.emoji)


@router.delete("/messages/{message_id}/reaction")
async def delete_reaction(
    message_id: int,
    request: Request,
    x_csrf_token: str | None = Header(None),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_api_user),
):
    check_csrf(request, x_csrf_token)
    return await _react(session, user, message_id, None)


async def _react(session: AsyncSession, user: User, message_id: int, emoji: str | None):
    try:
        return await chat.react(session, user, message_id, emoji)
    except chat.NotFound as e:
        raise HTTPException(404, "message not found") from e
    except chat.ChatError as e:
        raise HTTPException(e.status, e.message) from e
    except ratelimit.RateLimited as e:
        return _limited(e)


@router.put("/room/{room_id}/notice")
async def put_notice(
    room_id: int,
    body: NoticeIn,
    request: Request,
    x_csrf_token: str | None = Header(None),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_api_user),
):
    """방 공지 한 줄 — 방장만. 빈 글이면 내린다."""
    check_csrf(request, x_csrf_token)
    try:
        return {"notice": await chat.set_notice(session, user, room_id, body.text)}
    except chat.NotFound as e:
        raise HTTPException(404, "conversation not found") from e
    except chat.ChatError as e:
        raise HTTPException(e.status, e.message) from e
    except ratelimit.RateLimited as e:
        return _limited(e)
