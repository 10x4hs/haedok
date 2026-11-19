"""앱 아래 막대(휴대폰) · 왼쪽 레일(데스크톱)의 칸과 피드의 '새 글' 점.

막대는 5칸(홈 · 탐색 · 피드 · 서재 · 메시지) — Apple HIG·Material 모두 3~5칸을 권한다.
'나'는 레일에만 칸으로 있고 휴대폰에서는 위쪽 프로필 사진으로 연다. 만들기는 메뉴에 두지 않는다 —
피드 머리의 '+ 올리기'(와 프로필)에서만 연다(만들기가 두 곳이면 헷갈린다).
조사 메모: scratchpad/v3/nav-research.md
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request
from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.session import get_session
from app.models import User
from app.models_posts import ReadingPost
from app.web import templates

FEED_COOKIE = "feed_seen"  # "<내 id>.<그때 가장 최근 게시물 id>" — 피드를 연 순간을 기억한다(DB 표 없이)


@dataclass(frozen=True)
class NavItem:
    key: str
    href: str
    label: str
    on: bool = False
    badge: int = 0  # 숫자 알약(메시지)
    dot: bool = False  # 작은 점(피드: 친구가 새로 올림)
    rail_only: bool = False  # 휴대폰 막대에서는 숨긴다(나)


def _under(path: str, *prefixes: str) -> bool:
    return any(path == p or path.startswith(p.rstrip("/") + "/") for p in prefixes)


def nav_items(request: Request, user: User | None, path: str) -> list[NavItem]:
    from app.chat import chat_enabled, chat_unread
    from app.posts import feature_posts

    posts_on, chat_on = feature_posts(), chat_enabled()
    me_href = "/profile" if posts_on else "/me"
    mine = user is not None and (_under(path, "/me", "/profile") or path == f"/u/{user.id}")
    items = [
        NavItem("home", "/", "홈", on=path == "/"),
        NavItem("discover", "/discover", "탐색", on=_under(path, "/discover", "/search", "/store", "/shelf")),
    ]
    if posts_on:
        items.append(
            NavItem(
                "feed", "/feed", "피드", on=_under(path, "/feed", "/p", "/posts"), dot=feed_dot(request, user)
            )
        )
    items.append(
        NavItem(
            "library",
            "/library",
            "서재",
            on=_under(path, "/library", "/races", "/join", "/together", "/leaderboard", "/upload"),
        )
    )
    if chat_on:
        items.append(
            NavItem(
                "chat",
                "/chat",
                "메시지",
                on=_under(path, "/chat", "/friends"),
                badge=chat_unread(request, user),
            )
        )
    items.append(NavItem("me", me_href, "나", on=mine, rail_only=True))
    return items


def badge_text(n: int) -> str:
    return str(n) if n < 100 else "99+"


def feed_dot(request: Request, user: User | None) -> bool:
    if user is None:
        return False
    got = getattr(request.state, "feed_dot", None)
    return bool(got and got[0] == user.id and got[1])


def _seen(raw: str | None, uid: int) -> int:
    """쿠키 값에서 '내가 본 마지막 게시물 id'. 다른 사람의 쿠키거나 망가졌으면 0."""
    try:
        who, last = (raw or "").split(".", 1)
        return int(last) if int(who) == uid else 0
    except ValueError:
        return 0


def _cookie(scope) -> str | None:
    raw = dict(scope.get("headers") or []).get(b"cookie", b"").decode("latin-1")
    for part in raw.split(";"):
        k, _, v = part.strip().partition("=")
        if k == FEED_COOKIE:
            return v
    return None


async def friends_posted_since(session, uid: int, last_id: int) -> bool:
    """친구가 last_id 뒤에 올린, 내게 보이는 게시물이 하나라도 있나(EXISTS 한 번)."""
    from app.services import posts as svc

    friends, blocked = await svc.circle(session, uid)
    friends -= blocked
    if not friends:
        return False
    q = select(ReadingPost.id).where(
        svc._visible(uid, friends, blocked), ReadingPost.author_id.in_(friends), ReadingPost.id > last_id
    )
    return (await session.scalar(q.limit(1))) is not None


async def latest_post_id(session) -> int:
    return (await session.scalar(select(func.max(ReadingPost.id)))) or 0


class FeedDot:
    """화면(HTML GET)을 그리기 전에 '친구 새 글' 점을 정한다 — ChatMenuDot 과 같은 방식.

    /feed 를 열면 그때의 마지막 게시물 id 를 쿠키에 적고, 다른 화면에서는 그 뒤로 친구가 올린 글이
    있는지만 본다. 새 표·마이그레이션 없이 사람마다(브라우저마다) 기억한다.
    """

    SKIP = ("/api/", "/static/", "/health", "/avatars/", "/covers/", "/sw.js", "/manifest", "/media/")

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if not (scope["type"] == "http" and scope["method"] == "GET" and self._page(scope)):
            return await self.app(scope, receive, send)
        uid = (scope.get("session") or {}).get("uid")
        if not uid or not get_settings().feature_posts:
            return await self.app(scope, receive, send)
        at_feed = scope.get("path") == "/feed"
        try:
            async with self._session(scope) as session:
                if at_feed:
                    stamp = f"{uid}.{await latest_post_id(session)}"
                    dot = False
                else:
                    dot = await friends_posted_since(session, uid, _seen(_cookie(scope), uid))
        except Exception:  # noqa: BLE001 — 점 하나 때문에 화면이 깨지면 안 된다
            return await self.app(scope, receive, send)
        scope.setdefault("state", {})["feed_dot"] = (uid, dot)
        if not at_feed:
            return await self.app(scope, receive, send)

        secure = "; Secure" if scope.get("scheme") == "https" else ""
        cookie = f"{FEED_COOKIE}={stamp}; Path=/; Max-Age=31536000; HttpOnly; SameSite=Lax{secure}"

        async def send_with_cookie(message) -> None:
            if message["type"] == "http.response.start" and message.get("status", 200) < 400:
                message.setdefault("headers", [])
                message["headers"] = [*message["headers"], (b"set-cookie", cookie.encode("latin-1"))]
            await send(message)

        await self.app(scope, receive, send_with_cookie)

    def _page(self, scope) -> bool:
        path = scope.get("path", "")
        if path.startswith(self.SKIP):
            return False
        accept = dict(scope.get("headers") or []).get(b"accept", b"")
        return b"text/html" in accept or b"*/*" in accept or not accept

    def _session(self, scope):
        from contextlib import asynccontextmanager

        dep = scope["app"].dependency_overrides.get(get_session, get_session)  # 테스트의 DB 바꿔치기도 따른다

        @asynccontextmanager
        async def cm():
            gen = dep()
            session = await gen.__anext__()
            try:
                yield session
            finally:
                await gen.aclose()

        return cm()


templates.env.globals["nav_items"] = nav_items
templates.env.filters["badge_text"] = badge_text
