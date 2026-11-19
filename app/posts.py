"""읽은 책 게시물 화면 — /feed · /posts/new · /p/{id} · /u/{id} · /profile · /media/p/... · /admin/posts.

책 한 권 + 내 생각 + 사진 1~5장(없으면 책 표지)을 인스타그램처럼 올리고 본다.
사진은 파일 저장소가 없어 DB에 JPEG 바이트로 둔다(프로필 사진과 같은 방식) — 브라우저가 1080px로 줄이고,
서버가 EXIF · GPS 를 걷어 낸다(app/services/post_photos.py). 규칙은 app/services/posts.py 가 판정한다.
FEATURE_POSTS 가 꺼져 있으면 이 라우터의 모든 주소가 404이고, /me 의 '내 프로필' 줄도 사라진다.
"""

import asyncio
from urllib.parse import quote
from urllib.parse import quote as quote_url

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app import admin, auth
from app.api.deps import get_http_client, get_sources
from app.core import ratelimit
from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.models import User
from app.models_posts import ReadingPost
from app.services import bookstore
from app.services import feed as feed_svc
from app.services import post_photos as photo_rules
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
templates.env.filters["josa"] = feed_svc.josa

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
    if back.startswith(("/feed", "/p/", "/u/", "/profile")) and "//" not in back and "\\" not in back:
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
    aspect = src.get("aspect") if src.get("aspect") in svc.ASPECTS else "4:5"
    return {
        "aspect": aspect,
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
        max_photos=photo_rules.MAX_PHOTOS,
        max_photo_bytes=photo_rules.MAX_BYTES,
        photos_left=max(0, photo_rules.DAILY_PHOTOS - await svc.photos_today(session, user.id)),
        aspects=svc.ASPECTS,
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


@router.get("/posts/books")
async def book_search(
    q: str = Query("", max_length=100),
    sources: dict[str, BookSource] = Depends(get_sources),
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_session),
    client: httpx.AsyncClient = Depends(get_http_client),
    user: User = Depends(auth.require_user),
) -> JSONResponse:
    """올리기 2단계의 책 찾기 — 화면을 다시 그리지 않아야 1단계에서 고른 사진이 남는다(JSON)."""
    from app.web import cover

    term = q.strip()
    found, errors = (await _search(session, client, sources, settings, user, term)) if term else ([], {})
    items = []
    for value, pick, kind in found:
        c = cover(pick.title, pick.author)
        items.append(
            {
                "value": value,
                "title": pick.title,
                "author": pick.author or "",
                "cover": pick.cover or "",
                "kind": kind,
                "palette": c["palette"],
                "pattern": c["pattern"],
            }
        )
    return JSONResponse({"items": items, "partial": bool(errors)})


async def _read_photos(photos: list[UploadFile]) -> list[photo_rules.CleanPhoto]:
    """올린 사진들(보낸 순서 = 보여 줄 순서). 한 장이라도 안 맞으면 전체를 거절한다."""
    files = [f for f in photos if f is not None and (f.filename or f.size)]
    if len(files) > photo_rules.MAX_PHOTOS:
        raise svc.PostError(f"사진은 {photo_rules.MAX_PHOTOS}장까지 올릴 수 있어요")
    out = []
    for f in files:
        raw = await f.read(photo_rules.MAX_BYTES + 1)
        try:
            out.append(photo_rules.check_upload(raw))
        except photo_rules.PhotoError as e:
            raise svc.PostError(str(e)) from e
    return out


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
    aspect: str = Form("4:5"),
    photos: list[UploadFile] = File([]),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> HTMLResponse | RedirectResponse | JSONResponse:
    """한 번의 multipart 요청으로 글 + 사진(최대 5장 × 400 KB = 2 MB)을 받는다 — Vercel 4.5 MB 한도 안."""
    auth.check_csrf(request, csrf)
    raw = {"book": book, "status": status, "rating": rating, "caption": caption, "quote": quote}
    raw["visibility"], raw["aspect"] = visibility, aspect
    as_json = _wants_json(request)
    try:
        clean = await _read_photos(photos)
        pick = await svc.resolve_pick(session, book)
        post = await svc.create(
            session, user, pick, svc.validate(status, rating, caption, quote, visibility), clean
        )
    except ratelimit.RateLimited as e:
        if as_json:
            return JSONResponse({"detail": e.message}, status_code=429)
        return await _compose(request, session, user, _draft(raw), error=e.message, status_code=429)
    except svc.PostError as e:
        if as_json:
            return JSONResponse({"detail": str(e)}, status_code=400)
        return await _compose(request, session, user, _draft(raw), error=str(e), status_code=400)
    if as_json:
        return JSONResponse({"url": f"/p/{post.id}?msg={quote_url('게시물을 올렸어요')}"})
    return _to(f"/p/{post.id}", msg="게시물을 올렸어요")


# ---------------------------------------------------------------- 사진


@router.get("/media/p/{post_id}/{idx}.jpg")
async def photo(
    post_id: int,
    idx: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> Response:
    """게시물 사진 — 게시물을 볼 수 있는 사람에게만(아니면 404, 있는지도 알리지 않는다).

    주소에 내용 해시(?v=)가 붙어 있어 오래 캐시한다. 바뀌면 주소가 바뀐다.
    """
    await _post(session, user, post_id)
    data = await svc.photo_bytes(session, post_id, idx) if 0 <= idx < photo_rules.MAX_PHOTOS else None
    if data is None:
        raise HTTPException(404)
    return Response(
        data,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "private, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )


# ---------------------------------------------------------------- 게시물


@router.get("/p/{post_id}", response_class=HTMLResponse)
async def post_page(
    request: Request,
    post_id: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> HTMLResponse:
    post = await _post(session, user, post_id)
    threads = await svc.comments_of(session, user.id, post)
    return _render(
        request,
        "posts/post.html",
        user,
        c=await svc.card(session, user.id, post),
        faces=await svc.face_ids(session, user.id),
        comments=threads,
        comment_total=svc.comment_total(threads),
        friends=await svc.share_targets(session, user.id),
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
    return _to("/profile", msg="게시물을 지웠어요")


@router.post("/p/{post_id}/like", response_model=None)
async def like(
    request: Request,
    post_id: int,
    csrf: str = Form(""),
    back: str = Form(""),
    want: str = Form(""),  # "1": 그림을 두 번 탭했다 — 켜기만 한다
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> RedirectResponse | JSONResponse:
    auth.check_csrf(request, csrf)
    post = await _post(session, user, post_id)
    to = _back(back, f"/p/{post_id}")
    try:
        liked, count = await svc.toggle_like(session, user, post, want=True if want == "1" else None)
    except ratelimit.RateLimited as e:
        if _wants_json(request):
            return JSONResponse({"detail": e.message}, status_code=429)
        return _to(to, error=e.message)
    if _wants_json(request):
        return JSONResponse({"liked": liked, "count": count})
    return RedirectResponse(to + f"#post-{post_id}", status_code=303)


@router.get("/p/{post_id}/comments", response_class=HTMLResponse)
async def comments_sheet(
    request: Request,
    post_id: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> HTMLResponse:
    """휴대폰 피드의 댓글 시트 안쪽(조각 HTML) — 시트가 열릴 때 불러온다."""
    post = await _post(session, user, post_id)
    threads = await svc.comments_of(session, user.id, post)
    return templates.TemplateResponse(
        request,
        "posts/_comments.html",
        {
            "request": request,
            "user": user,
            "csrf": auth.csrf_token(request),
            "p": post,
            "comments": threads,
            "comment_total": svc.comment_total(threads),
            "faces": await svc.face_ids(session, user.id),
            "mine": post.author_id == user.id,
            "reasons": svc.REASONS,
            "max_comment": svc.MAX_COMMENT,
            "sheet": True,
        },
    )


def _err(e: Exception) -> str:
    return getattr(e, "message", None) or str(e)


@router.post("/p/{post_id}/comments", response_model=None)
async def comment(
    request: Request,
    post_id: int,
    csrf: str = Form(""),
    body: str = Form(""),
    parent_id: int | None = Form(None),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> RedirectResponse | JSONResponse:
    auth.check_csrf(request, csrf)
    post = await _post(session, user, post_id)
    try:
        c = await svc.add_comment(session, user, post, body, parent_id)
    except (svc.PostError, ratelimit.RateLimited) as e:
        if _wants_json(request):
            return JSONResponse(
                {"detail": _err(e)}, status_code=429 if isinstance(e, ratelimit.RateLimited) else 400
            )
        return _to(f"/p/{post_id}", error=_err(e), anchor="#comments")
    if _wants_json(request):
        return JSONResponse({"id": c.id})
    return RedirectResponse(f"/p/{post_id}#c{c.id}", status_code=303)


@router.post("/p/{post_id}/comments/{comment_id}/like", response_model=None)
async def like_comment(
    request: Request,
    post_id: int,
    comment_id: int,
    csrf: str = Form(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> RedirectResponse | JSONResponse:
    auth.check_csrf(request, csrf)
    post = await _post(session, user, post_id)
    try:
        liked, count = await svc.toggle_comment_like(session, user, post, comment_id)
    except svc.NotFound as e:
        raise HTTPException(404) from e
    except ratelimit.RateLimited as e:
        if _wants_json(request):
            return JSONResponse({"detail": e.message}, status_code=429)
        return _to(f"/p/{post_id}", error=e.message, anchor="#comments")
    if _wants_json(request):
        return JSONResponse({"liked": liked, "count": count})
    return RedirectResponse(f"/p/{post_id}#c{comment_id}", status_code=303)


@router.post("/p/{post_id}/comments/{comment_id}/report")
async def report_comment(
    request: Request,
    post_id: int,
    comment_id: int,
    csrf: str = Form(""),
    reason: str = Form(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> RedirectResponse:
    auth.check_csrf(request, csrf)
    post = await _post(session, user, post_id)
    try:
        await svc.report_comment(session, user, post, comment_id, reason)
    except svc.NotFound as e:
        raise HTTPException(404) from e
    except (svc.PostError, ratelimit.RateLimited) as e:
        return _to(f"/p/{post_id}", error=_err(e), anchor="#comments")
    return _to(f"/p/{post_id}", msg="댓글 신고를 받았어요. 운영자가 살펴볼게요", anchor="#comments")


@router.post("/p/{post_id}/save", response_model=None)
async def save(
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
        saved = await svc.toggle_save(session, user, post)
    except ratelimit.RateLimited as e:
        if _wants_json(request):
            return JSONResponse({"detail": e.message}, status_code=429)
        return _to(to, error=e.message)
    if _wants_json(request):
        return JSONResponse({"saved": saved})
    return RedirectResponse(to + f"#post-{post_id}", status_code=303)


@router.post("/p/{post_id}/send", response_model=None)
async def send(
    request: Request,
    post_id: int,
    csrf: str = Form(""),
    friend_id: int = Form(0),
    back: str = Form(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> RedirectResponse | JSONResponse:
    """종이비행기 — 고른 친구에게 개인 채팅으로 게시물 링크를 보낸다. 친구가 아니면 404."""
    auth.check_csrf(request, csrf)
    post = await _post(session, user, post_id)
    to = _back(back, f"/p/{post_id}")
    try:
        await svc.send_to_friend(session, user, post, friend_id)
    except svc.NotFound as e:
        raise HTTPException(404) from e
    except Exception as e:  # 채팅의 거절(속도 제한 · 빈 글)
        from app.services import chat as chat_service

        if not isinstance(e, (ratelimit.RateLimited, chat_service.ChatError)):
            raise
        if _wants_json(request):
            return JSONResponse({"detail": _err(e)}, status_code=429)
        return _to(to, error=_err(e))
    if _wants_json(request):
        return JSONResponse({"sent": True})
    return _to(to, msg="보냈어요")


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
async def my_profile_short(user: User = Depends(auth.require_user)) -> RedirectResponse:
    return RedirectResponse("/profile", status_code=303)


async def _profile(
    request: Request,
    session: AsyncSession,
    user: User,
    user_id: int,
    tab: str,
    before: int | None,
    page: int = 0,
) -> HTMLResponse:
    """프로필 한 화면(인스타의 순서): 머리(사진 · 숫자 3개) → 이름 · 소개 → 버튼 → 내 동그라미들
    → 아이콘 탭(게시물 · 소식 · 저장됨 — 소식 · 저장됨은 내 프로필에서만) → 격자."""
    try:
        prof = await svc.profile(session, user.id, user_id)
    except svc.NotFound as e:
        raise HTTPException(404, "사람을 찾을 수 없어요") from e
    me = prof.state == "self"
    tab = tab if (me and tab in ("feed", "saved")) else "posts"
    grid, cards, cursor, more = [], [], None, False
    if tab == "feed":
        cards, cursor = await svc.feed(session, user.id, "friends", before)
    elif tab == "saved":
        grid, more = await svc.saved_grid(session, user.id, page)
    else:
        grid, cursor = await svc.grid(session, user.id, user_id, before)
    settings = get_settings()
    return _render(
        request,
        "posts/profile.html",
        user,
        prof=prof,
        me=me,
        base="/profile" if me else f"/u/{user_id}",
        tab=tab,
        faces=await svc.face_ids(session, user.id),
        grid=grid,
        cards=cards,
        cursor=cursor,
        more=more,
        page=page,
        paged=bool(before) or page > 0,
        mine=await svc.tiles(session, user.id) if me else None,
        chat_on=settings.feature_chat,
        friends=await svc.share_targets(session, user.id) if tab == "feed" else [],
        **_notes(request),
    )


@router.get("/profile", response_class=HTMLResponse)
async def my_profile(
    request: Request,
    tab: str = "posts",
    before: int | None = Query(None, ge=1),
    page: int = Query(0, ge=0, le=1000),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> HTMLResponse:
    """하단 메뉴 '나' — 내 프로필이 먼저, 설정은 톱니 버튼 뒤(/me)."""
    return await _profile(request, session, user, user.id, tab, before, page)


@router.get("/profile/edit", response_class=HTMLResponse)
async def edit_profile(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> HTMLResponse:
    """프로필 편집 — 소개(≤ 150자). 사진 · 이름은 설정(/me)에서 바꾼다(그곳의 규칙을 그대로)."""
    return _render(
        request,
        "posts/profile_edit.html",
        user,
        bio=await svc.bio_of(session, user.id),
        max_bio=svc.MAX_BIO,
        faces={user.id},
        **_notes(request),
    )


@router.post("/profile/bio")
async def save_bio(
    request: Request,
    csrf: str = Form(""),
    bio: str = Form(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> RedirectResponse:
    auth.check_csrf(request, csrf)
    try:
        await svc.set_bio(session, user, bio)
    except (svc.PostError, ratelimit.RateLimited) as e:
        return _to("/profile/edit", error=_err(e))
    return _to("/profile", msg="소개를 저장했어요")


@router.get("/u/{user_id}", response_class=HTMLResponse)
async def profile_page(
    request: Request,
    user_id: int,
    tab: str = "posts",
    before: int | None = Query(None, ge=1),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> HTMLResponse:
    return await _profile(request, session, user, user_id, tab, before)


FEED_TABS = ("mix", "friends", "all")  # 전체(기본, 친구 + 다른 사람) · 친구 · 탐색(격자)
HIDE_COOKIE = "feed_hide_"  # + 모듈 이름: 하루 동안 그 제안 모듈을 숨긴다(DB 표 없이)


def _tab(raw: str) -> str:
    return raw if raw in FEED_TABS else "mix"  # 예전 주소 ?tab=rec 도 전체로


def _hidden(request: Request) -> set[str]:
    return {k for k in feed_svc.MODULES if request.cookies.get(HIDE_COOKIE + k) == "1"}


async def _stream(
    request: Request, session: AsyncSession, user: User, tab: str, before: int | None, cur: str | None
) -> dict:
    """피드 한 쪽의 내용 — 처음 그릴 때와 이어 읽기 조각(/feed/more)이 같이 쓴다."""
    out: dict = {"tab": tab, "items": [], "tiles": [], "cursor": None, "count": 0}
    hidden = _hidden(request)
    if tab == "all":
        out["tiles"], out["cursor"] = await svc.explore(session, user.id, before)
    elif tab == "friends":
        page_no = 1 if before else 0
        out["items"], out["cursor"], out["count"] = await feed_svc.friends_page(
            session, user, before, hidden, page_no
        )
    else:
        out["items"], out["cursor"], out["count"] = await feed_svc.mixed_page(session, user, cur, hidden)
    if tab == "friends" and any(i.kind == "caught" for i in out["items"]):
        out["caught_tiles"] = await feed_svc.popular_tiles(session, user.id, 3)
    return out


def _more_href(st: dict) -> str | None:
    if st["cursor"] is None:
        return None
    if st["tab"] == "mix":
        return f"/feed?cur={st['cursor']}"
    return f"/feed?tab={st['tab']}&before={st['cursor']}"


@router.get("/feed", response_class=HTMLResponse)
async def feed_page(
    request: Request,
    tab: str = "mix",
    before: int | None = Query(None, ge=1),
    cur: str | None = Query(None, max_length=40),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> HTMLResponse:
    """피드 — 앱의 소셜 홈. 전체(친구 먼저 + 다른 사람의 공개 글) · 친구 · 탐색(격자).

    머리: + 올리기 · 메시지(안 읽은 수) · 같이 읽기. 흐름 안에는 활동 소식과 제안 모듈."""
    from app.chat import chat_unread

    tab = _tab(tab)
    st = await _stream(request, session, user, tab, before, cur)
    paged = bool(before) or bool(cur)
    first = tab != "all" and not paged
    friend_n = await svc.friend_count(session, user.id)
    thin = tab == "friends" and first and st["count"] < feed_svc.THIN
    settings = get_settings()
    return _render(
        request,
        "posts/feed.html",
        user,
        **st,
        more_href=_more_href(st),
        paged=paged,
        faces=await svc.face_ids(session, user.id),
        friends=await svc.share_targets(session, user.id) if tab != "all" else [],
        stories=await feed_svc.stories(session, user) if first else [],
        chips=await feed_svc.reading_now(session, user.id) if first else [],
        newest=max((i.card.post.id for i in st["items"] if i.kind == "post"), default=0),
        thin=thin,
        lonely=first and friend_n == 0 and st["count"] == 0,
        people=await feed_svc.people(session, user.id) if thin else [],
        popular=await feed_svc.popular_tiles(session, user.id, 6) if thin else [],
        side=await feed_svc.side(session, user),
        together=await feed_svc.recruits(session, user.id, 5),
        chat_on=settings.feature_chat,
        board_on=settings.feature_board,
        unread=chat_unread(request, user) if settings.feature_chat else 0,
        **_notes(request),
    )


def _fragment(request: Request, user: User, faces: set[int], **ctx) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "posts/_stream.html",
        {
            "request": request,
            "user": user,
            "csrf": auth.csrf_token(request),
            "faces": faces,
            "chat_on": get_settings().feature_chat,
            "board_on": get_settings().feature_board,
            **ctx,
        },
    )


@router.get("/feed/more", response_class=HTMLResponse)
async def feed_more(
    request: Request,
    tab: str = "mix",
    before: int | None = Query(None, ge=1),
    cur: str | None = Query(None, max_length=40),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> HTMLResponse:
    """이어 읽기 조각(바닥에 가까워지면 자동으로) — 다음 쪽 칸들과 그다음 '더 보기' 링크."""
    tab = "friends" if tab == "friends" else "mix"
    st = await _stream(request, session, user, tab, before, cur)
    faces = await svc.face_ids(session, user.id)
    return _fragment(request, user, faces, **st, more_href=_more_href(st), paged=True)


@router.get("/feed/new")
async def feed_new(
    after: int = Query(0, ge=0),
    tab: str = "mix",
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> JSONResponse:
    """새 게시물 알약 — after 뒤로 올라온 게시물 수만(가볍게, 60초마다). 전체 탭은 다른 사람의 공개 글까지."""
    n = await feed_svc.new_count(session, user.id, after, mixed=tab != "friends") if after else 0
    return JSONResponse({"count": n}, headers={"Cache-Control": "no-store"})


@router.get("/feed/fresh", response_class=HTMLResponse)
async def feed_fresh(
    request: Request,
    after: int = Query(0, ge=1),
    tab: str = "mix",
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> HTMLResponse:
    """알약을 누르면 맨 위에 끼워 넣을 새 게시물들(조각) — 친구 글 먼저."""
    tab = "friends" if tab == "friends" else "mix"
    cards = await feed_svc.fresh(session, user.id, after, mixed=tab == "mix")
    faces = await svc.face_ids(session, user.id)
    items = [feed_svc.Item("post", card=c) for c in cards]
    return _fragment(request, user, faces, tab=tab, items=items, more_href=None, paged=True, fresh=True)


@router.post("/feed/hide/{key}", response_model=None)
async def feed_hide(
    request: Request,
    key: str,
    csrf: str = Form(""),
    user: User = Depends(auth.require_user),
) -> RedirectResponse | JSONResponse:
    """제안 모듈의 × — 하루 동안 그 모듈을 숨긴다(쿠키 하나, 서버에 남기지 않는다)."""
    auth.check_csrf(request, csrf)
    if key not in feed_svc.MODULES:
        raise HTTPException(404)
    resp: RedirectResponse | JSONResponse = (
        JSONResponse({"hidden": key}) if _wants_json(request) else RedirectResponse("/feed", status_code=303)
    )
    resp.set_cookie(
        HIDE_COOKIE + key,
        "1",
        max_age=86_400,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    return resp


# ---------------------------------------------------------------- 관리자


@router.get("/admin/posts", response_class=HTMLResponse)
async def admin_list(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(admin.require_admin),
) -> HTMLResponse:
    return admin._page(
        request,
        user,
        "posts",
        rows=await svc.reported(session),
        comments=await svc.reported_comments(session),
    )


@router.post("/admin/posts/comments/{comment_id}/{action}")
async def admin_comment_action(
    request: Request,
    comment_id: int,
    action: str,
    csrf: str = Form(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(admin.require_admin),
) -> RedirectResponse:
    auth.check_csrf(request, csrf)
    from app.models_posts import PostComment

    c = await session.get(PostComment, comment_id)
    if c is None or action not in ("hide", "resolve"):
        raise HTTPException(404)
    await svc.admin_comment_act(session, c, action)
    return admin._back("/admin/posts", msg="댓글을 내렸어요" if action == "hide" else "처리했어요")


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
