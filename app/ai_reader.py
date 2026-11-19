"""읽는 중의 AI(JSON) — 리더가 부른다. 규칙은 app/services/reader_ai.py 에 있다.

FEATURE_AI_READER · FEATURE_AI_RAG 가 꺼져 있거나 AI 가 설정되지 않았으면 모두 404, 리더에도 입구가 없다.
AI 를 부르는 길은 모두 하루 상한을 쓰고, 상한 · AI 장애는 200 + kind(capped · down) 로 조용히 알린다.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app import auth
from app.db.session import get_session
from app.models import Book, User
from app.realtime import broker
from app.services import books as book_service
from app.services import reader_ai, social
from app.web import templates

router = APIRouter(prefix="/api/v1/books/{book_id}/ai", include_in_schema=False)

templates.env.globals["ai_reader_on"] = reader_ai.on
templates.env.globals["ai_reader_away"] = reader_ai.away

NO_STORE = {"Cache-Control": "no-store"}


def _require_on() -> None:
    if not reader_ai.on():
        raise HTTPException(404)


async def _book(session, book_id: int, user: User) -> Book:
    book = await session.get(Book, book_id)
    if book is None or not book_service.can_read(book, user.id):
        raise HTTPException(404, "책을 찾을 수 없어요")
    return book


def _out(r: reader_ai.Result, **extra) -> JSONResponse:
    body = {"kind": r.kind, "answer": r.text, "pages": r.pages, "cached": r.cached} | extra
    return JSONResponse(body, headers=NO_STORE)


class QuickIn(BaseModel):
    chunk_index: int
    anchor: int = 0
    action: Literal["explain", "word", "summary"]
    selection: str = ""


@router.post("/quick")
async def quick(
    book_id: int,
    body: QuickIn,
    request: Request,
    x_csrf_token: str | None = Header(None),
    session=Depends(get_session),
    user: User = Depends(auth.require_api_user),
) -> JSONResponse:
    _require_on()
    auth.check_csrf(request, x_csrf_token)
    book = await _book(session, book_id, user)
    try:
        r = await reader_ai.quick(
            session, user, book, body.chunk_index, body.anchor, body.action, body.selection
        )
    except reader_ai.ReaderAIError as e:
        raise HTTPException(422, str(e)) from e
    return _out(
        r, note_id=r.note_id, label=reader_ai.ACTIONS[body.action], chunk=body.chunk_index, anchor=body.anchor
    )


@router.get("/notes")
async def list_notes(
    book_id: int,
    chunk: int | None = None,
    session=Depends(get_session),
    user: User = Depends(auth.require_api_user),
) -> JSONResponse:
    _require_on()
    book = await _book(session, book_id, user)
    items = [reader_ai.note_out(n) for n in await reader_ai.notes(session, user, book, chunk)]
    return JSONResponse({"items": items}, headers=NO_STORE)


async def _note(session, book: Book, user: User, note_id: int):
    n = await reader_ai.my_note(session, user, book, note_id)
    if n is None:
        raise HTTPException(404, "노트를 찾을 수 없어요")  # 남의 노트도 같은 답 — 있는지조차 알리지 않는다
    return n


@router.get("/notes/{note_id}")
async def get_note(
    book_id: int, note_id: int, session=Depends(get_session), user: User = Depends(auth.require_api_user)
) -> JSONResponse:
    _require_on()
    book = await _book(session, book_id, user)
    return JSONResponse(reader_ai.note_out(await _note(session, book, user, note_id)), headers=NO_STORE)


@router.post("/notes/{note_id}/save")
async def save_note(
    book_id: int,
    note_id: int,
    request: Request,
    x_csrf_token: str | None = Header(None),
    session=Depends(get_session),
    user: User = Depends(auth.require_api_user),
) -> JSONResponse:
    _require_on()
    auth.check_csrf(request, x_csrf_token)
    book = await _book(session, book_id, user)
    n = await reader_ai.save(session, await _note(session, book, user, note_id))
    return JSONResponse(reader_ai.note_out(n))


@router.delete("/notes/{note_id}")
async def delete_note(
    book_id: int,
    note_id: int,
    request: Request,
    x_csrf_token: str | None = Header(None),
    session=Depends(get_session),
    user: User = Depends(auth.require_api_user),
) -> JSONResponse:
    _require_on()
    auth.check_csrf(request, x_csrf_token)
    book = await _book(session, book_id, user)
    await reader_ai.remove(session, await _note(session, book, user, note_id))
    return JSONResponse({"deleted": note_id})


@router.post("/notes/{note_id}/share")
async def share_note(
    book_id: int,
    note_id: int,
    request: Request,
    x_csrf_token: str | None = Header(None),
    session=Depends(get_session),
    user: User = Depends(auth.require_api_user),
) -> JSONResponse:
    _require_on()
    auth.check_csrf(request, x_csrf_token)
    book = await _book(session, book_id, user)
    n = await _note(session, book, user, note_id)
    try:
        r = await reader_ai.share(session, user, book, n)
    except social.ReactionError as e:
        raise HTTPException(422, str(e)) from e
    others = await social.circle(session, user.id, book.id) - {user.id}
    # 본문은 싣지 않는다 — 친구는 자기 스포일러 가드로 /reactions 에서 다시 읽는다(반응과 같다)
    await broker.publish(
        book.id, others,
        {"type": "reaction.added", "chunk": r.chunk_index, "user_id": user.id, "nickname": user.nickname,
         "avatar": social.avatar_path(user.id, user.avatar)},
    )  # fmt: skip
    return JSONResponse({"note": reader_ai.note_out(n), "reaction": social._out(r, user.nickname, user.id)})


async def _book_level(kind: str, book_id: int, request: Request, token: str | None, session, user: User):
    _require_on()
    auth.check_csrf(request, token)
    book = await _book(session, book_id, user)
    fn = {"recap": reader_ai.recap, "characters": reader_ai.characters, "reflect": reader_ai.reflect}[kind]
    try:
        r = await fn(session, user, book)
    except reader_ai.ReaderAIError as e:
        raise HTTPException(422, str(e)) from e
    return _out(r, items=r.items)


@router.post("/recap")
async def recap(
    book_id: int, request: Request, x_csrf_token: str | None = Header(None),
    session=Depends(get_session), user: User = Depends(auth.require_api_user),
) -> JSONResponse:  # fmt: skip
    return await _book_level("recap", book_id, request, x_csrf_token, session, user)


@router.post("/characters")
async def characters(
    book_id: int, request: Request, x_csrf_token: str | None = Header(None),
    session=Depends(get_session), user: User = Depends(auth.require_api_user),
) -> JSONResponse:  # fmt: skip
    return await _book_level("characters", book_id, request, x_csrf_token, session, user)


@router.post("/reflect")
async def reflect(
    book_id: int, request: Request, x_csrf_token: str | None = Header(None),
    session=Depends(get_session), user: User = Depends(auth.require_api_user),
) -> JSONResponse:  # fmt: skip
    return await _book_level("reflect", book_id, request, x_csrf_token, session, user)
