"""책에게 물어보기(/books/{id}/ask, 리더의 시트) · 벡터 채우기 관리(/admin/ai).

FEATURE_AI_RAG 가 꺼져 있거나 AI 가 설정되지 않았으면(ai.enabled() False) 입구가 숨고 주소는 404.
'나를 위한 다음 책' 줄은 탐색 · 내 프로필이 app/services/recs.row() 로 그린다.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import admin, auth
from app.core.config import get_settings
from app.db.session import get_session
from app.models import Book, User
from app.models_ai import AskLog
from app.services import ai, rag, recs, social, vectors
from app.services import books as book_service
from app.web import _render, templates

router = APIRouter(include_in_schema=False)

templates.env.globals["ai_rag_on"] = recs.on
if get_settings().feature_ai_rag and ("/admin/ai", "AI") not in admin.NAV:
    admin.NAV.append(("/admin/ai", "AI"))

FLASH = {"capped": rag.CAPPED, "down": rag.DOWN, "unread": rag.NOT_READ, "empty": "질문을 적어 주세요."}


def _require_on() -> None:
    if not recs.on():
        raise HTTPException(404)


async def _book_for(session: AsyncSession, book_id: int, user: User) -> Book:
    """이 사람이 읽을 수 있는 책만 — 올린 파일은 올린 사람만(남의 파일은 있는지조차 알리지 않는다)."""
    book = await session.get(Book, book_id)
    if book is None or not book_service.can_read(book, user.id):
        raise HTTPException(404, "책을 찾을 수 없어요")
    return book


async def _state(session: AsyncSession, user: User, book: Book) -> dict:
    verified = await social.verified_of(session, user.id, book.id)
    items = [rag.as_item(r) for r in await rag.recent(session, user.id, book.id)]
    return {
        "items": items,
        "read_upto": verified + 1,  # 다 읽은 쪽(1부터, 0 = 아직 없음)
        "notice": rag.UPLOAD_NOTICE if book.owner_id is not None else None,
    }


# --- 전체 화면 ---------------------------------------------------------------------------


@router.get("/books/{book_id}/ask", response_class=HTMLResponse)
async def ask_page(
    request: Request,
    book_id: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> HTMLResponse:
    _require_on()
    book = await _book_for(session, book_id, user)
    st = await _state(session, user, book)
    return _render(
        request, "ai/ask.html", user, book=book, flash=FLASH.get(request.query_params.get("m", "")), **st
    )


@router.post("/books/{book_id}/ask")
async def ask_form(
    request: Request,
    book_id: int,
    question: str = Form(""),
    csrf: str = Form(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> RedirectResponse:
    _require_on()
    auth.check_csrf(request, csrf)
    book = await _book_for(session, book_id, user)
    if not question.strip():
        return RedirectResponse(f"/books/{book_id}/ask?m=empty", status_code=303)
    a = await rag.ask(session, user, book, question)
    suffix = f"?m={a.kind}" if a.kind in FLASH else "#latest"
    return RedirectResponse(f"/books/{book_id}/ask{suffix}", status_code=303)


@router.post("/books/{book_id}/ask/clear")
async def ask_clear_form(
    request: Request,
    book_id: int,
    csrf: str = Form(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> RedirectResponse:
    _require_on()
    auth.check_csrf(request, csrf)
    book = await _book_for(session, book_id, user)
    await rag.forget(session, user.id, book.id)
    return RedirectResponse(f"/books/{book_id}/ask", status_code=303)


# --- 리더 시트(JSON) ---------------------------------------------------------------------


class AskIn(BaseModel):
    question: str = ""


@router.get("/api/v1/books/{book_id}/ask")
async def ask_history(
    book_id: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_api_user),
) -> JSONResponse:
    _require_on()
    book = await _book_for(session, book_id, user)
    return JSONResponse(await _state(session, user, book), headers={"Cache-Control": "no-store"})


@router.post("/api/v1/books/{book_id}/ask")
async def ask_api(
    request: Request,
    book_id: int,
    body: AskIn,
    x_csrf_token: str | None = Header(None),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_api_user),
) -> JSONResponse:
    _require_on()
    auth.check_csrf(request, x_csrf_token)
    book = await _book_for(session, book_id, user)
    question = " ".join(body.question.split())
    if not question:
        raise HTTPException(422, "질문을 적어 주세요")
    a = await rag.ask(session, user, book, question)
    return JSONResponse(a.as_dict() | {"question": question[: rag.MAX_QUESTION]})


@router.delete("/api/v1/books/{book_id}/ask")
async def ask_forget(
    request: Request,
    book_id: int,
    x_csrf_token: str | None = Header(None),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_api_user),
) -> JSONResponse:
    _require_on()
    auth.check_csrf(request, x_csrf_token)
    book = await _book_for(session, book_id, user)
    return JSONResponse({"deleted": await rag.forget(session, user.id, book.id)})


# --- 관리자: 벡터 채우기 -------------------------------------------------------------------


def _require_flag() -> None:
    if not get_settings().feature_ai_rag:
        raise HTTPException(404)


@router.get("/admin/ai", response_class=HTMLResponse)
async def admin_ai(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(admin.require_admin),
) -> HTMLResponse:
    _require_flag()
    s = get_settings()
    fake = s.ai_provider == "fake"
    checks = [
        ("AI_PROVIDER", s.ai_provider or "꺼짐", ai.enabled()),
        ("AI_API_KEY", "넣어 두었어요" if s.ai_api_key else "없어요", bool(s.ai_api_key) or fake),
        ("AI_CHAT_MODEL", s.ai_chat_model, bool(s.ai_chat_model)),
        ("AI_EMBED_MODEL", f"{s.ai_embed_model} · {s.ai_embed_dim}차원", bool(s.ai_embed_model)),
        ("AI_DAILY_REQUESTS", f"한 사람 하루 {s.ai_daily_requests}번", s.ai_daily_requests > 0),
    ]
    progress = await vectors.book_progress(session)
    pieces, books = await vectors.chunk_stats(session)
    asked = int(await session.scalar(select(func.count()).select_from(AskLog)) or 0)
    return admin._page(
        request,
        user,
        "ai",
        checks=checks,
        progress=progress,
        pieces=pieces,
        piece_books=books,
        asked=asked,
        ai_on=ai.enabled(),
        batch=vectors.BATCH,
        dialect=vectors.dialect_of(session),
    )


@router.post("/admin/ai/fill")
async def admin_ai_fill(
    request: Request,
    csrf: str = Form(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(admin.require_admin),
) -> RedirectResponse:
    _require_flag()
    auth.check_csrf(request, csrf)
    if not ai.enabled():
        return admin._back("/admin/ai", err="AI 설정(AI_PROVIDER · AI_API_KEY)이 없어요")
    try:
        p = await vectors.fill_books(session, vectors.BATCH)
    except ai.AIUnavailable as e:
        await session.rollback()
        return admin._back("/admin/ai", err=f"임베딩을 받지 못했어요: {e}")
    if not p.added:
        return admin._back("/admin/ai", msg=f"모두 채워져 있어요 ({p.done:,} / {p.total:,})")
    return admin._back("/admin/ai", msg=f"{p.added}권을 채웠어요 ({p.done:,} / {p.total:,})")
