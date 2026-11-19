"""같이 읽을 사람 모집 게시판(/together) — 당근처럼 읽고 싶은 책을 띄워 두고, 누구나 참가한다.

FEATURE_BOARD가 꺼져 있으면 이 라우터의 모든 주소가 404다(관리자 목록 포함).
"""

from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app import admin, auth
from app.api.deps import get_sources
from app.core import ratelimit
from app.core.config import get_settings
from app.db.session import get_session
from app.models import Book, Shelf, User
from app.models_board import RecruitPost
from app.services import board as board_service
from app.services import books as book_service
from app.services.search import FULLTEXT_SOURCES
from app.sources.base import BookSource, SourceError
from app.web import _render, popular


def require_feature() -> None:
    if not get_settings().feature_board:
        raise HTTPException(404)


router = APIRouter(include_in_schema=False, dependencies=[Depends(require_feature)])

ADMIN_NAV = ("/admin/together", "모집 신고")
if ADMIN_NAV not in admin.NAV:  # 관리자 메뉴에 한 칸(app/admin.py는 건드리지 않는다)
    admin.NAV.append(ADMIN_NAV)

# 글쓰기 · 참가 · 신고 속도 제한 (10분 창)
POST_LIMIT = 5
POST_MSG = "모집 글을 너무 자주 올렸어요. 10분 뒤에 다시 해 주세요"
REPORT_LIMIT = 10
REPORT_MSG = "신고를 너무 자주 보냈어요. 10분 뒤에 다시 해 주세요"


def _to(path: str, msg: str | None = None, error: str | None = None) -> RedirectResponse:
    if error:
        path += f"?error={quote(error)}"
    elif msg:
        path += f"?msg={quote(msg)}"
    return RedirectResponse(path, status_code=303)


def _notes(request: Request) -> dict:
    return {"msg": request.query_params.get("msg"), "error": request.query_params.get("error")}


async def _post_or_404(session: AsyncSession, post_id: int) -> RecruitPost:
    post = await session.get(RecruitPost, post_id)
    if post is None:
        raise HTTPException(404, "모집 글을 찾을 수 없어요")
    return post


# --- 게시판 ---


@router.get("/together")
async def board_moved(request: Request) -> RedirectResponse:
    """예전 주소 — 모집 게시판은 커뮤니티의 '같이 읽기' 갈래가 되었다(검색어는 그대로).

    글(/together/{id}) · 쓰기(/together/new) 주소는 그대로다."""
    q = request.url.query
    return RedirectResponse("/community/together" + (f"?{q}" if q else ""), status_code=308)


@router.get("/community/together", response_class=HTMLResponse)
async def board(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> HTMLResponse:
    posts = await board_service.board(session, user.id)
    return _render(
        request, "board/board.html", user, posts=posts, community="together", **_notes(request)
    )


async def _picks(session: AsyncSession, user: User) -> tuple[list[Book], list[dict]]:
    """고를 수 있는 책: 읽는 중인 고전(누구나 읽을 수 있는 것만) + 인기 고전."""
    reading = [
        b
        for e, b in await book_service.library_of(session, user.id)
        if e.shelf == Shelf.READING and board_service.readable(b)
    ]
    return reading[:9], await popular(session, 12)


async def _new_page(
    request: Request,
    session: AsyncSession,
    user: User,
    form: dict | None = None,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    reading, classics = await _picks(session, user)
    form = form or {}
    chosen = form.get("book") or ""
    # 서재에 없는 책을 골라 들어왔으면 맨 앞에 보여 준다(예: /together/new?book=b:12)
    extra = None
    if chosen.startswith("b:") and chosen[2:].isdigit() and all(f"b:{b.id}" != chosen for b in reading):
        book = await session.get(Book, int(chosen[2:]))
        extra = book if board_service.readable(book) else None
    return _render(
        request,
        "board/new.html",
        user,
        status_code=status_code,
        reading=([extra] if extra else []) + reading,
        classics=classics,
        paces=board_service.PACES,
        pace_label=board_service.pace_label,
        capacities=range(board_service.MIN_CAPACITY, board_service.MAX_CAPACITY + 1),
        form={
            "book": chosen,
            "pitch": form.get("pitch", ""),
            "pace": int(form.get("pace", board_service.DEFAULT_PACE)),
            "capacity": int(form.get("capacity", board_service.DEFAULT_CAPACITY)),
        },
        max_pitch=board_service.MAX_PITCH,
        open_count=await board_service.open_count(session, user.id),
        max_open=board_service.MAX_OPEN_POSTS,
        error=error,
    )


@router.get("/together/new", response_class=HTMLResponse)
async def new_form(
    request: Request,
    book: str = "",
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> HTMLResponse:
    return await _new_page(request, session, user, {"book": book})


async def _resolve_book(session: AsyncSession, sources: dict[str, BookSource], choice: str) -> Book | None:
    """폼의 책 값: b:<books.id> 또는 c:<source>:<external_id>(인기 고전 — 아직 없으면 가져온다)."""
    if choice.startswith("b:") and choice[2:].isdigit():
        return await session.get(Book, int(choice[2:]))
    if choice.startswith("c:"):
        source, _, external_id = choice[2:].partition(":")
        src = sources.get(source)
        if src is None or source not in FULLTEXT_SOURCES or not external_id:
            return None
        try:
            return await book_service.import_book(session, src, external_id)
        except (SourceError, httpx.HTTPError) as e:
            raise board_service.BoardError("책을 가져오지 못했어요. 잠시 뒤 다시 해 주세요") from e
    return None


@router.post("/together", response_class=HTMLResponse, response_model=None)
async def create(
    request: Request,
    book: str = Form(""),
    pitch: str = Form(""),
    pace: int = Form(board_service.DEFAULT_PACE),
    capacity: int = Form(board_service.DEFAULT_CAPACITY),
    csrf: str = Form(""),
    sources: dict[str, BookSource] = Depends(get_sources),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> HTMLResponse | RedirectResponse:
    auth.check_csrf(request, csrf)
    form = {"book": book, "pitch": pitch, "pace": pace, "capacity": capacity}
    try:
        await ratelimit.check(session, f"recruit:{user.id}", POST_LIMIT, ratelimit.WINDOW_S, POST_MSG)
        if await board_service.open_count(session, user.id) >= board_service.MAX_OPEN_POSTS:
            # 책을 가져오기(외부 호출) 전에 먼저 막는다 — 같은 검사를 create_post가 한 번 더 한다
            raise board_service.BoardError(board_service.TOO_MANY_MSG)
        chosen = await _resolve_book(session, sources, book)
        post = await board_service.create_post(session, user, chosen, pitch, pace, capacity)
    except ratelimit.RateLimited as e:
        return await _new_page(request, session, user, form, e.message, status_code=429)
    except board_service.BoardError as e:
        return await _new_page(request, session, user, form, str(e), status_code=400)
    return _to(f"/together/{post.id}", msg="모집 글을 올렸어요")


@router.get("/together/{post_id}", response_class=HTMLResponse)
async def detail(
    request: Request,
    post_id: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> HTMLResponse:
    v = await board_service.view(session, post_id, user.id)
    if v is None:
        raise HTTPException(404, "모집 글을 찾을 수 없어요")
    return _render(request, "board/post.html", user, v=v, reasons=board_service.REASONS, **_notes(request))


@router.post("/together/{post_id}/join")
async def join(
    request: Request,
    post_id: int,
    csrf: str = Form(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> RedirectResponse:
    auth.check_csrf(request, csrf)
    try:
        await ratelimit.check_join(session, user.id)  # 초대 코드 참가와 같은 한도
    except ratelimit.RateLimited as e:
        return _to(f"/together/{post_id}", error=e.message)
    post = await _post_or_404(session, post_id)
    room_id, book_id = post.room_id, post.book_id
    if reason := await board_service.join(session, post, user):
        return _to(f"/together/{post_id}", error=reason)
    await book_service.shelve(session, user.id, book_id)
    return RedirectResponse(f"/races/{room_id}", status_code=303)


async def _act(request: Request, csrf: str, session: AsyncSession, post_id: int) -> RecruitPost:
    auth.check_csrf(request, csrf)
    return await _post_or_404(session, post_id)


@router.post("/together/{post_id}/leave")
async def leave(
    request: Request,
    post_id: int,
    csrf: str = Form(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> RedirectResponse:
    post = await _act(request, csrf, session, post_id)
    try:
        await board_service.leave(session, post, user)
    except board_service.BoardError as e:
        return _to(f"/together/{post_id}", error=str(e))
    return _to(f"/together/{post_id}", msg="나왔어요. 언제든 다시 참가할 수 있어요")


@router.post("/together/{post_id}/close")
async def close(
    request: Request,
    post_id: int,
    csrf: str = Form(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> RedirectResponse:
    post = await _act(request, csrf, session, post_id)
    try:
        await board_service.close(session, post, user)
    except board_service.BoardError as e:
        return _to(f"/together/{post_id}", error=str(e))
    return _to(f"/together/{post_id}", msg="모집을 마감했어요. 들어온 사람들과는 계속 읽어요")


@router.post("/together/{post_id}/delete")
async def remove(
    request: Request,
    post_id: int,
    csrf: str = Form(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> RedirectResponse:
    post = await _act(request, csrf, session, post_id)
    try:
        await board_service.remove(session, post, user)
    except board_service.BoardError as e:
        return _to(f"/together/{post_id}", error=str(e))
    return _to("/community/together", msg="모집 글을 지웠어요")


@router.post("/together/{post_id}/report")
async def report(
    request: Request,
    post_id: int,
    reason: str = Form(""),
    csrf: str = Form(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> RedirectResponse:
    auth.check_csrf(request, csrf)
    try:
        await ratelimit.check(
            session, f"recruit-report:{user.id}", REPORT_LIMIT, ratelimit.WINDOW_S, REPORT_MSG
        )
    except ratelimit.RateLimited as e:
        return _to(f"/together/{post_id}", error=e.message)
    post = await _post_or_404(session, post_id)
    try:
        await board_service.report(session, post, user, reason)
    except board_service.BoardError as e:
        return _to(f"/together/{post_id}", error=str(e))
    return _to(f"/together/{post_id}", msg="신고를 받았어요. 운영자가 살펴볼게요")


# --- 관리자 ---


@router.get("/admin/together", response_class=HTMLResponse)
async def admin_list(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(admin.require_admin),
) -> HTMLResponse:
    rows = await board_service.reported(session)
    return admin._page(request, user, "board", rows=rows)


@router.post("/admin/together/{post_id}/close")
async def admin_close(
    request: Request,
    post_id: int,
    csrf: str = Form(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(admin.require_admin),
) -> RedirectResponse:
    auth.check_csrf(request, csrf)
    post = await _post_or_404(session, post_id)
    await board_service.admin_close(session, post)
    return admin._back("/admin/together", msg="모집을 내렸어요")
