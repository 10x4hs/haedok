"""친구 + 채팅 화면 — /friends · /chat(개인 · 방 탭) · /chat/{room|dm}/{key} · /admin/chat-reports.

JSON API는 app/api/chat.py. 이 모듈의 router 하나가 둘 다 싣는다(main.py에 한 줄).
FEATURE_CHAT 이 꺼져 있으면 모든 경로가 404이고 메뉴 · 링크가 사라진다.
"""

import datetime as dt
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import admin, auth
from app.api import chat as chat_api
from app.api.chat import require_chat
from app.core import ratelimit
from app.core.config import get_settings
from app.db.session import get_session
from app.models import Room, User
from app.models_chat import ChatMessage, ChatReport, ChatThread
from app.services import chat
from app.web import _render, templates

pages = APIRouter(include_in_schema=False, dependencies=[Depends(require_chat)])


# ---------------------------------------------------------------- 메뉴의 '채팅' 항목과 안 읽은 점


def chat_enabled() -> bool:
    return get_settings().feature_chat


def chat_unread(request: Request, user: User | None) -> int:
    """ChatMenuDot 이 화면을 그리기 전에 세어 둔 값(로그인한 바로 그 사람의 것일 때만)."""
    if user is None:
        return 0
    got = getattr(request.state, "chat_unread", None)
    return got[1] if got and got[0] == user.id else 0


KST = dt.timezone(dt.timedelta(hours=9))


def chat_when(t: dt.datetime | None) -> str:
    """목록의 마지막 활동(상대 시각): '방금' · '5분' · '3시간' · '어제' · '11.13'(한국 날짜)."""
    if t is None:
        return ""
    t = t if t.tzinfo else t.replace(tzinfo=dt.UTC)
    now = chat.clock()
    secs = (now - t).total_seconds()
    if secs < 60:
        return "방금"
    if secs < 3600:
        return f"{int(secs // 60)}분"
    local, today = t.astimezone(KST).date(), now.astimezone(KST).date()
    if local == today:
        return f"{int(secs // 3600)}시간"
    if local == today - dt.timedelta(days=1):
        return "어제"
    return f"{local.month}.{local.day}"


templates.env.filters["chat_when"] = chat_when
templates.env.globals["chat_enabled"] = chat_enabled
templates.env.globals["chat_unread"] = chat_unread


class ChatMenuDot:
    """화면(HTML GET) 요청에서만, 그리기 전에 안 읽은 메시지 수를 한 번 센다.

    템플릿은 동기라 그 안에서 DB를 물을 수 없어서 여기서 미리 세어 request.state 에 둔다.
    다른 화면에서 따로 폴링하지 않는다. 세션을 읽어야 하므로 SessionMiddleware 안쪽에 둔다
    (main.py 에서 SessionMiddleware 보다 먼저 add_middleware).
    """

    SKIP = ("/api/", "/static/", "/health", "/avatars/", "/covers/", "/sw.js", "/manifest")

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http" and scope["method"] == "GET" and self._page(scope):
            uid = (scope.get("session") or {}).get("uid")
            if uid and get_settings().feature_chat:
                try:
                    n = await self._count(scope, uid)
                except Exception:  # noqa: BLE001 — 점 하나 때문에 화면이 깨지면 안 된다
                    n = 0
                scope.setdefault("state", {})["chat_unread"] = (uid, n)
        await self.app(scope, receive, send)

    def _page(self, scope) -> bool:
        path = scope.get("path", "")
        if path.startswith(self.SKIP):
            return False
        accept = dict(scope.get("headers") or []).get(b"accept", b"")
        return b"text/html" in accept or b"*/*" in accept or not accept

    async def _count(self, scope, uid: int) -> int:
        app = scope["app"]
        dep = app.dependency_overrides.get(get_session, get_session)  # 테스트의 DB 바꿔치기도 따른다
        gen = dep()
        session = await gen.__anext__()
        try:
            return await chat.unread_total(session, uid)
        finally:
            await gen.aclose()


# ---------------------------------------------------------------- 친구


def _back(path: str, msg: str | None = None, err: str | None = None) -> RedirectResponse:
    if msg:
        path += ("&" if "?" in path else "?") + f"msg={quote(msg)}"
    elif err:
        path += ("&" if "?" in path else "?") + f"err={quote(err)}"
    return RedirectResponse(path, status_code=303)


def _invite_href(book) -> str:
    """'같이 읽기 초대' — 모집 게시판이 켜져 있으면 그 책으로 모집 글 쓰기, 아니면 대결 열기."""
    if get_settings().feature_board:
        return f"/together/new?book=b:{book.id}" if book is not None else "/together/new"
    return "/races#newRace"


@pages.get("/friends", response_class=HTMLResponse)
async def friends_page(
    request: Request,
    q: str = "",
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> HTMLResponse:
    q = q.strip()[:40]
    people = await chat.people(session, user.id)
    cards = await chat.friend_cards(session, user.id, week=True)
    incoming = [(u, await chat.shared_rooms(session, user.id, u.id)) for u in people.incoming]
    return _render(
        request,
        "friends.html",
        user,
        people=people,
        cards=cards,
        incoming=incoming,
        q=q,
        found=await chat.search(session, user.id, q) if q else [],
        roommates=await chat.roommates(session, user.id),
        invite_href=_invite_href,
        board_on=get_settings().feature_board,
        goals_on=get_settings().feature_goals,
        msg=request.query_params.get("msg"),
        err=request.query_params.get("err"),
    )


ACTIONS = {
    "request": (chat.send_request, None),
    "accept": (chat.accept, "친구가 됐어요"),
    "decline": (chat.decline, "신청을 거절했어요"),
    "cancel": (chat.cancel, "신청을 취소했어요"),
    "remove": (chat.unfriend, "친구를 끊었어요"),
    "block": (chat.block, "차단했어요. 이제 이 사람의 메시지가 보이지 않아요"),
    "unblock": (chat.unblock, "차단을 풀었어요"),
}


@pages.post("/friends/{other_id}/{action}")
async def friend_action(
    other_id: int,
    action: str,
    request: Request,
    csrf: str = Form(""),
    back: str = Form(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> RedirectResponse:
    auth.check_csrf(request, csrf)
    if action not in ACTIONS:
        raise HTTPException(404)
    fn, done = ACTIONS[action]
    to = "/friends" + (f"?q={quote(back[:40])}" if back else "")
    try:
        result = await fn(session, user, other_id)
    except chat.NotFound as e:
        raise HTTPException(404) from e
    except chat.ChatError as e:
        return _back(to, err=e.message)
    except ratelimit.RateLimited as e:
        return _back(to, err=e.message)
    if action == "request":
        done = "친구가 됐어요" if result == "friends" else "친구 신청을 보냈어요"
    return _back(to, msg=done)


# ---------------------------------------------------------------- 채팅


async def _list_ctx(session: AsyncSession, user: User, tab: str) -> dict:
    """왼쪽 목록(좁은 화면에서는 /chat 한 화면): 탭 · 대화 · 친구 동그라미 줄."""
    tab = "room" if tab == "room" else "dm"
    other = "dm" if tab == "room" else "room"
    convs = await chat.conversations(session, user, tab)
    other_unread = sum(c["unread"] for c in await chat.conversations(session, user, other))
    return {
        "tab": tab,
        "convs": convs,
        "unread": {tab: sum(c["unread"] for c in convs), other: other_unread},
        "bubbles": await chat.friend_cards(session, user.id),
    }


@pages.get("/chat", response_class=HTMLResponse)
async def chat_home(
    request: Request,
    tab: str = "dm",
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> HTMLResponse:
    return _render(request, "chat.html", user, **await _list_ctx(session, user, tab))


async def _conv_page(
    request: Request, session: AsyncSession, user: User, kind: str, key: int, ch: str = "book"
) -> HTMLResponse:
    try:
        conv = await (chat.room_conv if kind == "room" else chat.dm_conv)(session, user, key)
    except chat.NotFound as e:
        raise HTTPException(404) from e
    ctx: dict = {"conv": conv, "owner": False, "channel": "chat", "members": [], "notice": None}
    if kind == "room":
        ctx["channel"] = "chat" if ch == "chat" else "book"
        ctx["my_page"] = await chat.social.verified_of(session, user.id, conv.room.book_id) + 1
        ctx["owner"] = conv.room.owner_id == user.id
        ctx["members"] = await chat.members(session, conv.room, user.id)
        ctx["channel_unread"] = await chat.channel_unread(session, user, conv)
        ctx["notice"] = conv.thread.notice if conv.thread else None
        names = [m["user"].nickname for m in ctx["members"]]
    else:
        names = [user.nickname, conv.other.nickname]
    ctx["names"] = names
    ctx["emojis"] = list(chat.social.EMOJIS)
    ctx["api"] = f"/api/v1/chat/{kind}/{key}/messages"
    ctx |= await _list_ctx(session, user, kind)
    return _render(request, "chat_thread.html", user, **ctx)


@pages.get("/chat/room/{room_id}", response_class=HTMLResponse)
async def room_chat(
    room_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> HTMLResponse:
    return await _conv_page(request, session, user, "room", room_id, request.query_params.get("ch", "book"))


@pages.get("/chat/dm/{other_id}", response_class=HTMLResponse)
async def dm_chat(
    other_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> HTMLResponse:
    return await _conv_page(request, session, user, "dm", other_id)


# ---------------------------------------------------------------- 관리자: 신고

if ("/admin/chat-reports", "신고") not in admin.NAV:
    admin.NAV.append(("/admin/chat-reports", "신고"))


@pages.get("/admin/chat-reports", response_class=HTMLResponse)
async def admin_reports(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(admin.require_admin),
) -> HTMLResponse:
    rows = (
        await session.execute(
            select(ChatReport, ChatMessage, ChatThread)
            .join(ChatMessage, ChatMessage.id == ChatReport.message_id)
            .join(ChatThread, ChatThread.id == ChatMessage.thread_id)
            .order_by(ChatReport.resolved, ChatReport.id.desc())
            .limit(200)
        )
    ).all()
    ids = {r.reporter_id for r, _, _ in rows} | {m.user_id for _, m, _ in rows}
    ids |= {u for _, _, t in rows for u in (t.user_low, t.user_high) if u}
    users = {u.id: u.nickname for u in await session.scalars(select(User).where(User.id.in_(ids or {0})))}
    rooms = {
        r.id: r.name
        for r in await session.scalars(
            select(Room).where(Room.id.in_({t.room_id for _, _, t in rows} or {0}))
        )
    }
    items = []
    for rep, msg, t in rows:
        where = (
            f"방 · {rooms.get(t.room_id, '?')}"
            if t.room_id
            else f"개인 · {users.get(t.user_low, '?')} ↔ {users.get(t.user_high, '?')}"
        )
        items.append(
            {
                "report": rep,
                "msg": msg,
                "where": where,
                "author": users.get(msg.user_id, "(탈퇴)"),
                "reporter": users.get(rep.reporter_id, "(탈퇴)"),
            }
        )
    return admin._page(request, user, "chat_reports", items=items)


@pages.post("/admin/chat-reports/{message_id}/{action}")
async def admin_report_action(
    message_id: int,
    action: str,
    request: Request,
    csrf: str = Form(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(admin.require_admin),
) -> RedirectResponse:
    auth.check_csrf(request, csrf)
    msg = await session.get(ChatMessage, message_id)
    if msg is None or action not in ("hide", "resolve"):
        raise HTTPException(404)
    if action == "hide" and msg.deleted_at is None:
        msg.deleted_at, msg.deleted_by = chat.clock(), user.id
    for rep in await session.scalars(select(ChatReport).where(ChatReport.message_id == message_id)):
        rep.resolved = True
    await session.commit()
    return admin._back("/admin/chat-reports", msg="메시지를 숨겼어요" if action == "hide" else "처리했어요")


router = APIRouter()
router.include_router(chat_api.router)
router.include_router(pages)
