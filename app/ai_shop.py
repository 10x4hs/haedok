"""AI 서점 도우미(/assistant) · 이번 달 독서(/me/recap) · 게시물 초안 화면.

AI 가 꺼져 있거나(ai.enabled() False) FEATURE_AI_SHOP 이 꺼져 있으면 모든 주소가 404 이고 입구도 숨는다.
규칙은 app/services/shop_ai.py(도우미) · app/services/recap.py(요약 · 초안)가 정한다.
"""

from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app import auth
from app.api.deps import get_http_client, get_sources
from app.core import ratelimit
from app.core.config import get_settings
from app.db.session import get_session
from app.models import Book, User
from app.services import ai, recap, shop_ai
from app.services import books as book_service
from app.services.search import FULLTEXT_SOURCES
from app.sources.base import BookSource, SourceError
from app.web import _render, templates


def ai_shop_on() -> bool:
    return get_settings().feature_ai_shop and ai.enabled()


def require_feature() -> None:
    if not ai_shop_on():
        raise HTTPException(404)


templates.env.globals["ai_shop_on"] = ai_shop_on
templates.env.globals["ai_unknown_price"] = shop_ai.UNKNOWN_PRICE
# 서점 구매 링크는 모두 이 필터(= shop_ai.affiliate, 제휴 값을 붙일 유일한 곳)를 거친다
templates.env.filters["buy_link"] = shop_ai.affiliate

router = APIRouter(include_in_schema=False, dependencies=[Depends(require_feature)])


def _wants_json(request: Request) -> bool:
    return "application/json" in request.headers.get("accept", "")


async def _friends(session: AsyncSession, user: User) -> list[User]:
    if not get_settings().feature_chat:
        return []
    from app.services import posts as post_service

    return await post_service.share_targets(session, user.id)


# ---------------------------------------------------------------- 서점 도우미


async def _assistant(
    request: Request, session: AsyncSession, user: User, turns: list[shop_ai.Turn], q: str = ""
) -> HTMLResponse:
    last = turns[-1] if turns else None
    return _render(
        request,
        "ai/assistant.html",
        user,
        turns=turns,
        state=shop_ai.pack(user.id, turns) if turns else "",
        chips=shop_ai.chips(last),
        examples=shop_ai.EXAMPLES,
        prefill=q[: shop_ai.MAX_Q],
        friends=await _friends(session, user),
        max_q=shop_ai.MAX_Q,
    )


@router.get("/assistant", response_class=HTMLResponse)
async def assistant(
    request: Request,
    q: str = Query("", max_length=300),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> HTMLResponse:
    """빈 대화. ?q= 는 입력칸에 미리 채우기만 한다(GET 은 AI 를 부르지 않는다)."""
    return await _assistant(request, session, user, [], q.strip())


@router.post("/assistant", response_class=HTMLResponse)
async def assistant_turn(
    request: Request,
    csrf: str = Form(""),
    q: str = Form("", max_length=600),
    chip: str = Form("", max_length=100),
    state: str = Form("", max_length=200_000),
    session: AsyncSession = Depends(get_session),
    client: httpx.AsyncClient = Depends(get_http_client),
    user: User = Depends(auth.require_user),
) -> HTMLResponse:
    """한 차례 묻고 답한다. q 가 비면 들고 있던 대화를 다시 그리기만 한다(새로고침 뒤 되살리기)."""
    auth.check_csrf(request, csrf)
    turns = shop_ai.unpack(user.id, state)
    q = " ".join((chip or q).split())[: shop_ai.MAX_Q]
    if q:
        turns = (turns + [await shop_ai.ask(session, client, user, q, turns)])[-shop_ai.KEEP_TURNS :]
    return await _assistant(request, session, user, turns)


@router.post("/assistant/shelve", response_model=None)
async def assistant_shelve(
    request: Request,
    csrf: str = Form(""),
    source: str = Form(""),
    external_id: str = Form("", max_length=512),
    sources: dict[str, BookSource] = Depends(get_sources),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> JSONResponse | RedirectResponse:
    """서재에 담기 — 무료 책을 가져와(이미 있으면 그대로) '읽는 중'으로 꽂는다.

    서점 책은 본문이 없어 담지 않는다."""
    auth.check_csrf(request, csrf)
    src = sources.get(source)
    if src is None or source not in FULLTEXT_SOURCES or shop_ai.book_of(source, external_id) is None:
        raise HTTPException(404)
    try:
        book = await book_service.import_book(session, src, external_id)
    except (SourceError, httpx.HTTPError):
        if _wants_json(request):
            return JSONResponse(
                {"detail": "지금은 책을 가져오지 못했어요. 잠시 뒤 다시 해 주세요"}, status_code=502
            )
        return RedirectResponse("/library?error=import", status_code=303)
    await book_service.shelve(session, user.id, book.id)
    if _wants_json(request):
        return JSONResponse({"shelved": True, "book_id": book.id})
    return RedirectResponse("/library", status_code=303)


@router.post("/assistant/send", response_model=None)
async def assistant_send(
    request: Request,
    csrf: str = Form(""),
    friend_id: int = Form(0),
    source: str = Form(""),
    external_id: str = Form("", max_length=512),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> JSONResponse | RedirectResponse:
    """친구에게 보내기 — 개인 채팅에 책 한 줄(제목은 우리 데이터에서).

    친구가 아니거나 채팅이 꺼져 있으면 404."""
    auth.check_csrf(request, csrf)
    if not get_settings().feature_chat:
        raise HTTPException(404)
    book = shop_ai.book_of(source, external_id)
    if book is None:
        raise HTTPException(404)
    from app.services import chat as chat_service

    try:
        conv = await chat_service.dm_conv(session, user, friend_id)
        await chat_service.send(session, user, conv, shop_ai.share_text(book))
    except chat_service.NotFound as e:
        raise HTTPException(404) from e
    except (ratelimit.RateLimited, chat_service.ChatError) as e:
        msg = getattr(e, "message", None) or str(e)
        if _wants_json(request):
            return JSONResponse({"detail": msg}, status_code=429)
        return RedirectResponse("/assistant", status_code=303)
    if _wants_json(request):
        return JSONResponse({"sent": True})
    return RedirectResponse(f"/chat/dm/{friend_id}", status_code=303)


# ---------------------------------------------------------------- 이번 달 독서


def _recap_url(month: recap.Month, **q: str) -> str:
    return "/me/recap?" + urlencode({"month": month.key, **q})


@router.get("/me/recap", response_class=HTMLResponse)
async def recap_page(
    request: Request,
    month: str = Query("", max_length=7),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> HTMLResponse:
    m = recap.parse_month(month)
    now = recap.this_month()
    data = await recap.numbers(session, user.id, m)
    item = recap.cached(user, m)
    return _render(
        request,
        "ai/recap.html",
        user,
        r=data,
        paragraph=item["text"] if item else None,
        can_make=not recap.made_today(user, m),
        prev=m.shift(-1),
        next=m.shift(1) if (m.year, m.month) < (now.year, now.month) else None,
        finished=await recap.finished_books(session, user.id),
        posts_on=get_settings().feature_posts,
        msg=request.query_params.get("msg"),
        error=request.query_params.get("error"),
    )


@router.post("/me/recap")
async def recap_make(
    request: Request,
    csrf: str = Form(""),
    month: str = Form("", max_length=7),
    session: AsyncSession = Depends(get_session),
    client: httpx.AsyncClient = Depends(get_http_client),
    user: User = Depends(auth.require_user),
) -> RedirectResponse:
    """AI 문단 만들기 · 새로 만들기(하루 한 번). 실패해도 화면으로 돌아가 조용히 알린다."""
    auth.check_csrf(request, csrf)
    m = recap.parse_month(month)
    data = await recap.numbers(session, user.id, m)
    if data.empty:
        return RedirectResponse(_recap_url(m, error="이 달에는 아직 요약할 기록이 없어요."), status_code=303)
    try:
        await recap.summarise(session, client, user, data)
    except recap.TooSoon as e:
        return RedirectResponse(_recap_url(m, error=str(e)), status_code=303)
    except ratelimit.RateLimited as e:
        return RedirectResponse(_recap_url(m, error=e.message), status_code=303)
    except ai.AIUnavailable:
        return RedirectResponse(
            _recap_url(m, error="지금은 AI가 답하지 못해요. 잠시 뒤에 다시 해 주세요."), 303
        )
    return RedirectResponse(_recap_url(m) + "#ai", status_code=303)


@router.post("/me/recap/draft")
async def caption_draft(
    request: Request,
    csrf: str = Form(""),
    book_id: int = Form(0),
    session: AsyncSession = Depends(get_session),
    client: httpx.AsyncClient = Depends(get_http_client),
    user: User = Depends(auth.require_user),
) -> RedirectResponse:
    """게시물 초안 — 글을 만들어 /posts/new 의 ② 단계에 채워 보내기만 한다. 게시물은 사용자가 직접 올린다."""
    auth.check_csrf(request, csrf)
    if not get_settings().feature_posts:
        raise HTTPException(404)
    book = await session.get(Book, book_id)
    if book is None or book.id not in {b.id for b in await recap.finished_books(session, user.id, 200)}:
        raise HTTPException(404)
    back = recap.this_month()
    try:
        d = await recap.draft(session, client, user, book)
    except ratelimit.RateLimited as e:
        return RedirectResponse(_recap_url(back, error=e.message) + "#drafts", status_code=303)
    except ai.AIUnavailable:
        msg = "지금은 AI가 초안을 쓰지 못해요. 잠시 뒤에 다시 해 주세요."
        return RedirectResponse(_recap_url(back, error=msg) + "#drafts", status_code=303)
    q = {"book": f"b:{book.id}", "status": "done", "caption": d.caption, "step": "2", "ai": "1"}
    if d.quote:
        q["quote"] = d.quote
    return RedirectResponse("/posts/new?" + urlencode(q), status_code=303)
