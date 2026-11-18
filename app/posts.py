"""읽은 책 게시물 화면 — /feed · /posts/new · /p/{id} · /u/{id} · /admin/posts.

책 한 권 + 내 생각을 인스타처럼 올린다. 그림은 책 표지(와 표지에서 나온 배경)뿐이다 —
이 호스팅에는 파일 저장소가 없어서 사진 올리기는 없다. 규칙은 app/services/posts.py 가 판정한다.
FEATURE_POSTS 가 꺼져 있으면 이 라우터의 모든 주소가 404이고, /me 의 '내 프로필' 줄도 사라진다.
"""

import asyncio
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app import admin, auth
from app.api.deps import get_http_client, get_sources
from app.core import ratelimit
from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.models import User
from app.models_posts import ReadingPost
from app.services import bookstore
from app.services import posts as svc
from app.services.search import search_all
from app.sources.base import BookSource
from app.web import _render, templates


def feature_posts() -> bool:
    return get_settings().feature_posts


def require_feature() -> None:
    if not feature_posts():
        raise HTTPException(404)


templates.env.globals["feature_posts"] = feature_posts
templates.env.globals["post_segments"] = svc.segments
templates.env.filters["post_when"] = svc.when

router = APIRouter(include_in_schema=False, dependencies=[Depends(require_feature)])

ADMIN_NAV = ("/admin/posts", "게시물 신고")
if ADMIN_NAV not in admin.NAV:  # 관리자 메뉴에 한 칸(app/admin.py는 건드리지 않는다)
    admin.NAV.append(ADMIN_NAV)

SEARCH_SHOWN = 24


def _to(path: str, msg: str | None = None, error: str | None = None, anchor: str = "") -> RedirectResponse:
    if error:
        path += ("&" if "?" in path else "?") + f"error={quote(error)}"
    elif msg:
        path += ("&" if "?" in path else "?") + f"msg={quote(msg)}"
    return RedirectResponse(path + anchor, status_code=303)


def _notes(request: Request) -> dict:
    return {"msg": request.query_params.get("msg"), "error": request.query_params.get("error")}


def _back(back: str, fallback: str) -> str:
    """돌아갈 곳 — 이 기능의 화면만(다른 사이트 · 다른 화면으로 튕기지 않는다)."""
    if back.startswith(("/feed", "/p/", "/u/")) and "//" not in back and "\\" not in back:
        return back[:200]
    return fallback


def _wants_json(request: Request) -> bool:
    return "application/json" in request.headers.get("accept", "")


async def _post(session: AsyncSession, user: User, post_id: int) -> ReadingPost:
    try:
        return await svc.visible_post(session, user.id, post_id)
    except svc.NotFound as e:
        raise HTTPException(404, "게시물을 찾을 수 없어요") from e


# ---------------------------------------------------------------- 올리기


async def _search(
    session: AsyncSession,
    client: httpx.AsyncClient,
    sources: dict[str, BookSource],
    settings: Settings,
    user: User,
    term: str,
) -> tuple[list[tuple[str, svc.BookPick, str]], dict]:
    """/search 와 같은 곳(무료 카탈로그 · 공개 도서관 · 서점)에서 찾고, 고른 값은 서명해 둔다."""
    from app import web

    found: list[tuple[str, svc.BookPick, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(pick: svc.BookPick, kind: str) -> None:
        key = (pick.source, pick.external_id)
        if key in seen or pick.source == "upload" or not pick.title:
            return
        seen.add(key)
        found.append((svc.sign_pick(pick), pick, kind))

    for b in web.CATALOG:
        if term in b["title"] or term in (b.get("author") or ""):
            cover = svc.safe_cover(b.get("cover"))
            add(svc.BookPick(b["source"], str(b["id"]), b["title"], b.get("author"), cover), "무료 고전")
            if len(found) >= 8:
                break
    result, live = await asyncio.gather(
        search_all(list(sources.values()), term, 12, settings.http_timeout_seconds),
        web._store_search(session, client, user, term),
    )
    for b in bookstore.merge(bookstore.local_search(web.STORE, term), live, web.STORE_INDEX):
        cover = svc.safe_cover(b.get("cover"))
        add(svc.BookPick("store", str(b["id"]), b["title"], b.get("author"), cover), "서점")
    for h in result.hits:
        m = h.meta
        authors = ", ".join(m.authors) or None
        add(svc.BookPick(m.source, m.external_id, m.title, authors, svc.safe_cover(m.cover_url)), "도서관")
    return found[:SEARCH_SHOWN], result.errors


def _draft(src) -> dict:
    """폼 초안 — 검색을 다시 해도 쓰던 글이 남는다(검색 폼이 숨은 칸으로 가져온다)."""
    rating = str(src.get("rating") or "")
    return {
        "book": (src.get("book") or "")[:4000],
        "status": src.get("status") if src.get("status") in svc.STATUSES else "done",
        "rating": rating if rating in {"1", "2", "3", "4", "5"} else "",
        "caption": (src.get("caption") or "")[: svc.MAX_CAPTION + 50],
        "quote": (src.get("quote") or "")[: svc.MAX_QUOTE + 50],
        "visibility": vis if (vis := src.get("visibility")) in svc.VISIBILITY else svc.DEFAULT_VISIBILITY,
    }


async def _compose(
    request: Request,
    session: AsyncSession,
    user: User,
    draft: dict,
    *,
    q: str = "",
    found: list | None = None,
    errors: dict | None = None,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    library = await svc.library_picks(session, user.id)
    library.sort(key=lambda x: x[0] != draft["book"])  # 고른 책을 맨 앞에(가로 줄에서 바로 보이게)
    chosen = None
    for value, pick, _ in library:
        if value == draft["book"]:
            chosen = pick
    if chosen is None and draft["book"].startswith("h:"):
        chosen = svc.unsign_pick(draft["book"])
        if chosen is not None and found is not None and all(v != draft["book"] for v, _, _ in found):
            found = [(draft["book"], chosen, "고른 책")] + found
        elif chosen is not None and found is None:
            found = [(draft["book"], chosen, "고른 책")]
    return _render(
        request,
        "posts/new.html",
        user,
        status_code=status_code,
        draft=draft,
        library=library,
        q=q,
        found=found,
        search_errors=errors or {},
        chosen=chosen,
        statuses=svc.STATUSES,
        visibilities=svc.VISIBILITY,
        max_caption=svc.MAX_CAPTION,
        max_quote=svc.MAX_QUOTE,
        error=error,
    )


@router.get("/posts/new", response_class=HTMLResponse)
async def new_post(
    request: Request,
    q: str = Query("", max_length=100),
    sources: dict[str, BookSource] = Depends(get_sources),
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_session),
    client: httpx.AsyncClient = Depends(get_http_client),
    user: User = Depends(auth.require_user),
) -> HTMLResponse:
    draft = _draft(request.query_params)
    term = q.strip()
    found, errors = (await _search(session, client, sources, settings, user, term)) if term else (None, {})
    return await _compose(request, session, user, draft, q=term, found=found, errors=errors)


@router.post("/posts", response_class=HTMLResponse, response_model=None)
async def create_post(
    request: Request,
    csrf: str = Form(""),
    book: str = Form(""),
    status: str = Form("done"),
    rating: str = Form(""),
    caption: str = Form(""),
    quote: str = Form(""),
    visibility: str = Form(svc.DEFAULT_VISIBILITY),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> HTMLResponse | RedirectResponse:
    auth.check_csrf(request, csrf)
    raw = {"book": book, "status": status, "rating": rating, "caption": caption, "quote": quote}
    raw["visibility"] = visibility
    try:
        pick = await svc.resolve_pick(session, book)
        post = await svc.create(session, user, pick, svc.validate(status, rating, caption, quote, visibility))
    except ratelimit.RateLimited as e:
        return await _compose(request, session, user, _draft(raw), error=e.message, status_code=429)
    except svc.PostError as e:
        return await _compose(request, session, user, _draft(raw), error=str(e), status_code=400)
    return _to(f"/p/{post.id}", msg="게시물을 올렸어요")


# ---------------------------------------------------------------- 게시물


@router.get("/p/{post_id}", response_class=HTMLResponse)
async def post_page(
    request: Request,
    post_id: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> HTMLResponse:
    post = await _post(session, user, post_id)
    return _render(
        request,
        "posts/post.html",
        user,
        c=await svc.card(session, user.id, post),
        faces=await svc.face_ids(session, user.id),
        comments=await svc.comments_of(session, user.id, post),
        reasons=svc.REASONS,
        visibilities=svc.VISIBILITY,
        statuses=svc.STATUSES,
        max_caption=svc.MAX_CAPTION,
        max_quote=svc.MAX_QUOTE,
        max_comment=svc.MAX_COMMENT,
        **_notes(request),
    )


@router.post("/p/{post_id}/edit")
async def edit_post(
    request: Request,
    post_id: int,
    csrf: str = Form(""),
    caption: str = Form(""),
    quote: str = Form(""),
    rating: str = Form(""),
    status: str = Form("done"),
    visibility: str = Form(svc.DEFAULT_VISIBILITY),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> RedirectResponse:
    auth.check_csrf(request, csrf)
    post = await _post(session, user, post_id)
    try:
        await svc.edit(session, user, post, svc.validate(status, rating, caption, quote, visibility))
    except svc.NotFound as e:
        raise HTTPException(404) from e
    except svc.PostError as e:
        return _to(f"/p/{post_id}", error=str(e))
    return _to(f"/p/{post_id}", msg="고쳤어요")


@router.post("/p/{post_id}/delete")
async def delete_post(
    request: Request,
    post_id: int,
    csrf: str = Form(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> RedirectResponse:
    auth.check_csrf(request, csrf)
    post = await _post(session, user, post_id)
    try:
        await svc.remove(session, user, post)
    except svc.NotFound as e:
        raise HTTPException(404) from e
    return _to(f"/u/{user.id}", msg="게시물을 지웠어요")


@router.post("/p/{post_id}/like", response_model=None)
async def like(
    request: Request,
    post_id: int,
    csrf: str = Form(""),
    back: str = Form(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> RedirectResponse | JSONResponse:
    auth.check_csrf(request, csrf)
    post = await _post(session, user, post_id)
    to = _back(back, f"/p/{post_id}")
    try:
        liked, count = await svc.toggle_like(session, user, post)
    except ratelimit.RateLimited as e:
        if _wants_json(request):
            return JSONResponse({"detail": e.message}, status_code=429)
        return _to(to, error=e.message)
    if _wants_json(request):
        return JSONResponse({"liked": liked, "count": count})
    return RedirectResponse(to + f"#post-{post_id}", status_code=303)


@router.post("/p/{post_id}/comments")
async def comment(
    request: Request,
    post_id: int,
    csrf: str = Form(""),
    body: str = Form(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> RedirectResponse:
    auth.check_csrf(request, csrf)
    post = await _post(session, user, post_id)
    try:
        c = await svc.add_comment(session, user, post, body)
    except (svc.PostError, ratelimit.RateLimited) as e:
        return _to(f"/p/{post_id}", error=getattr(e, "message", None) or str(e), anchor="#comments")
    return RedirectResponse(f"/p/{post_id}#c{c.id}", status_code=303)


@router.post("/p/{post_id}/comments/{comment_id}/delete")
async def delete_comment(
    request: Request,
    post_id: int,
    comment_id: int,
    csrf: str = Form(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> RedirectResponse:
    auth.check_csrf(request, csrf)
    post = await _post(session, user, post_id)
    try:
        await svc.delete_comment(session, user, post, comment_id)
    except svc.NotFound as e:
        raise HTTPException(404) from e
    return _to(f"/p/{post_id}", msg="댓글을 지웠어요", anchor="#comments")


@router.post("/p/{post_id}/report")
async def report(
    request: Request,
    post_id: int,
    csrf: str = Form(""),
    reason: str = Form(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> RedirectResponse:
    auth.check_csrf(request, csrf)
    post = await _post(session, user, post_id)
    try:
        await svc.report(session, user, post, reason)
    except (svc.PostError, ratelimit.RateLimited) as e:
        return _to(f"/p/{post_id}", error=getattr(e, "message", None) or str(e))
    return _to(f"/p/{post_id}", msg="신고를 받았어요. 운영자가 살펴볼게요")


# ---------------------------------------------------------------- 프로필 · 피드


@router.get("/u", include_in_schema=False)
async def my_profile(user: User = Depends(auth.require_user)) -> RedirectResponse:
    return RedirectResponse(f"/u/{user.id}", status_code=303)


@router.get("/u/{user_id}", response_class=HTMLResponse)
async def profile_page(
    request: Request,
    user_id: int,
    before: int | None = Query(None, ge=1),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> HTMLResponse:
    try:
        prof = await svc.profile(session, user.id, user_id)
    except svc.NotFound as e:
        raise HTTPException(404, "사람을 찾을 수 없어요") from e
    tiles, cursor = await svc.grid(session, user.id, user_id, before)
    settings = get_settings()
    grass = await svc.grass(session, user_id) if prof.state in ("self", "friends") else None
    return _render(
        request,
        "posts/profile.html",
        user,
        prof=prof,
        faces=await svc.face_ids(session, user.id),
        tiles=tiles,
        cursor=cursor,
        paged=bool(before),
        grass=grass,
        chat_on=settings.feature_chat,
        **_notes(request),
    )


@router.get("/feed", response_class=HTMLResponse)
async def feed_page(
    request: Request,
    tab: str = "friends",
    before: int | None = Query(None, ge=1),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> HTMLResponse:
    tab = "all" if tab == "all" else "friends"
    cards, cursor = await svc.feed(session, user.id, tab, before)
    return _render(
        request,
        "posts/feed.html",
        user,
        tab=tab,
        cards=cards,
        cursor=cursor,
        paged=bool(before),
        faces=await svc.face_ids(session, user.id),
        **_notes(request),
    )


# ---------------------------------------------------------------- 관리자


@router.get("/admin/posts", response_class=HTMLResponse)
async def admin_list(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(admin.require_admin),
) -> HTMLResponse:
    return admin._page(request, user, "posts", rows=await svc.reported(session))


@router.post("/admin/posts/{post_id}/{action}")
async def admin_action(
    request: Request,
    post_id: int,
    action: str,
    csrf: str = Form(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(admin.require_admin),
) -> RedirectResponse:
    auth.check_csrf(request, csrf)
    post = await session.get(ReadingPost, post_id)
    if post is None or action not in ("hide", "resolve"):
        raise HTTPException(404)
    await svc.admin_act(session, user, post, action)
    return admin._back("/admin/posts", msg="게시물을 내렸어요" if action == "hide" else "처리했어요")
