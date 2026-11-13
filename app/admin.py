"""관리자 화면(/admin) — 현황 · 사용자 · 대결방 · 메일.

ADMIN_EMAILS에 적힌 이메일로 로그인한 사람만 연다. 그 밖의 모든 요청(비로그인·일반 사용자)은
404로 답해 이 화면이 있다는 사실 자체를 드러내지 않는다. 역할 칼럼은 두지 않는다.
"""

import datetime as dt
import time
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import auth
from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.models import (
    Book,
    LibraryEntry,
    MailLog,
    RacePresence,
    RaceQuote,
    Room,
    RoomMember,
    SignupVerification,
    Team,
    User,
)
from app.web import _render, templates

router = APIRouter(prefix="/admin", include_in_schema=False)

NAV = [("/admin", "현황"), ("/admin/users", "사용자"), ("/admin/rooms", "대결방"), ("/admin/mail", "메일")]
RECENT_MAIL = 100
PAGE = 50


def is_admin(user: User | None, settings: Settings | None = None) -> bool:
    if user is None or not user.email:
        return False
    return user.email.strip().lower() in (settings or get_settings()).admin_email_set()


templates.env.globals["is_admin"] = is_admin


async def require_admin(
    user: User | None = Depends(auth.current_user),
    settings: Settings = Depends(get_settings),
) -> User:
    if not is_admin(user, settings):
        raise HTTPException(404)
    return user


def _fmt(value: dt.datetime | int | None) -> str:
    """날짜를 '11.13 14:05'처럼 짧게. signup_verifications는 유닉스 초로 들어온다."""
    if value is None:
        return "—"
    if isinstance(value, int | float):
        value = dt.datetime.fromtimestamp(value, dt.UTC)
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.UTC)
    return value.astimezone(dt.timezone(dt.timedelta(hours=9))).strftime("%m.%d %H:%M")


templates.env.filters["fmt_dt"] = _fmt


def _page(request: Request, user: User, name: str, **ctx) -> HTMLResponse:
    ctx |= {"nav": NAV, "msg": request.query_params.get("msg"), "err": request.query_params.get("err")}
    return _render(request, f"admin/{name}.html", user, **ctx)


def _back(path: str, msg: str | None = None, err: str | None = None) -> RedirectResponse:
    if msg:
        path += f"?msg={quote(msg)}"
    elif err:
        path += f"?err={quote(err)}"
    return RedirectResponse(path, status_code=303)


# --- 현황 ---


@router.get("", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_admin),
) -> HTMLResponse:
    now = int(time.time())
    since = dt.datetime.now(dt.UTC) - dt.timedelta(hours=24)
    counts = {
        "users": await session.scalar(select(func.count(User.id))),
        "pending": await session.scalar(
            select(func.count(SignupVerification.id)).where(SignupVerification.expires_at >= now)
        ),
        "expired": await session.scalar(
            select(func.count(SignupVerification.id)).where(SignupVerification.expires_at < now)
        ),
        "rooms": await session.scalar(select(func.count(Room.id))),
        "books": await session.scalar(select(func.count(Book.id)).where(Book.source != "upload")),
        "uploads": await session.scalar(select(func.count(Book.id)).where(Book.source == "upload")),
    }
    mail = (
        await session.execute(
            select(
                func.count(case((MailLog.ok.is_(True), 1))),
                func.count(case((MailLog.ok.is_(False), 1))),
            ).where(MailLog.created_at >= since)
        )
    ).one()
    counts |= {"mail_ok": mail[0] or 0, "mail_fail": mail[1] or 0}
    return _page(request, user, "dashboard", counts=counts)


# --- 사용자 ---


@router.get("/users", response_class=HTMLResponse)
async def users(
    request: Request,
    q: str = Query(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_admin),
) -> HTMLResponse:
    term = q.strip().lower()
    activity = (
        select(
            LibraryEntry.user_id,
            func.max(LibraryEntry.updated_at).label("last_seen"),
            func.count(LibraryEntry.id).label("books"),
        )
        .group_by(LibraryEntry.user_id)
        .subquery()
    )
    stmt = (
        select(User, activity.c.last_seen, activity.c.books)
        .outerjoin(activity, activity.c.user_id == User.id)
        .order_by(User.id.desc())
        .limit(PAGE)
    )
    pending_stmt = select(SignupVerification).order_by(SignupVerification.sent_at.desc()).limit(PAGE)
    if term:
        stmt = stmt.where(func.lower(User.email).contains(term))
        pending_stmt = pending_stmt.where(func.lower(SignupVerification.email).contains(term))
    rows = (await session.execute(stmt)).all()
    pending = (await session.scalars(pending_stmt)).all()
    return _page(
        request,
        user,
        "users",
        q=q,
        rows=[(u, last, books or 0) for u, last, books in rows],
        pending=pending,
        now=int(time.time()),
    )


@router.post("/signups/{pending_id}/approve")
async def approve_signup(
    request: Request,
    pending_id: int,
    csrf: str = Form(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_admin),
) -> RedirectResponse:
    """코드 확인 없이 가입을 승인한다 — 메일이 안 닿은 사람을 손으로 들여보낼 때."""
    auth.check_csrf(request, csrf)
    pending = await session.get(SignupVerification, pending_id)
    if pending is None:
        return _back("/admin/users", err="가입 대기를 찾을 수 없어요")
    session.add(User(nickname=pending.nickname, email=pending.email, password_hash=pending.password_hash))
    await session.delete(pending)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return _back("/admin/users", err=f"{pending.email}은 이미 가입된 이메일이에요")
    return _back("/admin/users", msg=f"{pending.email} 가입을 승인했어요")


@router.post("/signups/{pending_id}/delete")
async def delete_signup(
    request: Request,
    pending_id: int,
    csrf: str = Form(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_admin),
) -> RedirectResponse:
    auth.check_csrf(request, csrf)
    pending = await session.get(SignupVerification, pending_id)
    if pending is None:
        return _back("/admin/users", err="가입 대기를 찾을 수 없어요")
    email = pending.email
    await session.delete(pending)
    await session.commit()
    return _back("/admin/users", msg=f"{email} 가입 대기를 지웠어요")


@router.post("/users/{user_id}/delete")
async def delete_user(
    request: Request,
    user_id: int,
    csrf: str = Form(""),
    confirm: str = Form(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_admin),
) -> RedirectResponse:
    auth.check_csrf(request, csrf)
    if confirm != "1":
        return _back("/admin/users", err="삭제를 확인해 주세요")
    if user_id == user.id:
        return _back("/admin/users", err="내 계정은 여기서 지울 수 없어요")
    target = await session.get(User, user_id)
    if target is None:
        return _back("/admin/users", err="사용자를 찾을 수 없어요")
    label = target.email or target.nickname
    # rooms.owner_id · teams.created_by 는 CASCADE가 아니라 먼저 지운다 (나머지는 FK CASCADE)
    for room in await session.scalars(select(Room).where(Room.owner_id == user_id)):
        await session.delete(room)
    for team in await session.scalars(select(Team).where(Team.created_by == user_id)):
        await session.delete(team)
    await session.delete(target)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return _back("/admin/users", err=f"{label}에 연결된 데이터가 있어 지우지 못했어요")
    return _back("/admin/users", msg=f"{label} 계정을 지웠어요")


# --- 대결방 · 책 ---


@router.get("/rooms", response_class=HTMLResponse)
async def rooms(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_admin),
) -> HTMLResponse:
    members = select(RoomMember.room_id, func.count().label("n")).group_by(RoomMember.room_id).subquery()
    presence = (
        select(RacePresence.room_id, func.max(RacePresence.last_seen).label("last_seen"))
        .group_by(RacePresence.room_id)
        .subquery()
    )
    quotes = select(RaceQuote.room_id, func.count().label("n")).group_by(RaceQuote.room_id).subquery()
    stmt = (
        select(Room, Book.title, User.nickname, members.c.n, presence.c.last_seen, quotes.c.n)
        .join(Book, Book.id == Room.book_id)
        .join(User, User.id == Room.owner_id)
        .outerjoin(members, members.c.room_id == Room.id)
        .outerjoin(presence, presence.c.room_id == Room.id)
        .outerjoin(quotes, quotes.c.room_id == Room.id)
        .order_by(Room.id.desc())
        .limit(PAGE)
    )
    rows = [
        {"room": r, "title": t, "owner": o, "members": m or 0, "last_seen": ls, "quotes": qn or 0}
        for r, t, o, m, ls, qn in (await session.execute(stmt)).all()
    ]
    now = dt.datetime.now(dt.UTC)
    for row in rows:
        ends = row["room"].ends_at
        if ends is not None and ends.tzinfo is None:
            ends = ends.replace(tzinfo=dt.UTC)
        row["over"] = ends is not None and ends < now
    by_source = (
        await session.execute(
            select(Book.source, func.count(Book.id)).group_by(Book.source).order_by(Book.source)
        )
    ).all()
    uploads = (
        await session.execute(
            select(Book, User.nickname)
            .outerjoin(User, User.id == Book.owner_id)
            .where(Book.source == "upload")
            .order_by(Book.id.desc())
            .limit(PAGE)
        )
    ).all()
    return _page(request, user, "rooms", rows=rows, by_source=by_source, uploads=uploads)


# --- 메일 ---


def mail_check(settings: Settings) -> list[tuple[str, str, bool]]:
    """설정 점검 — (항목, 표시값, 괜찮은지). 비밀값은 절대 찍지 않고 '있음/없음'만 말한다."""
    smtp = bool(settings.smtp_user and settings.smtp_password)
    resend = bool(settings.resend_api_key)
    if smtp:
        transport = f"SMTP ({settings.smtp_host}:{settings.smtp_port})"
    elif resend:
        transport = "Resend"
    else:
        transport = "로그에만 남김 (SMTP_USER·RESEND_API_KEY 둘 다 없음)"
    default_key = settings.secret_key == Settings.model_fields["secret_key"].default
    driver = urlsplit(settings.database_url).scheme or "?"
    return [
        ("메일 전송 방식", transport, smtp or resend),
        ("SMTP_USER / SMTP_PASSWORD", "있음" if smtp else "없음", smtp),
        ("RESEND_API_KEY", "있음" if resend else "없음", resend),
        ("SECRET_KEY", "기본값 (바꿔야 해요)" if default_key else "설정됨", not default_key),
        ("COOKIE_SECURE", "켜짐" if settings.cookie_secure else "꺼짐", settings.cookie_secure),
        ("DATABASE_URL 드라이버", driver, driver.startswith("postgresql")),
    ]


@router.get("/mail", response_class=HTMLResponse)
async def mail(
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    user: User = Depends(require_admin),
) -> HTMLResponse:
    logs = (await session.scalars(select(MailLog).order_by(MailLog.id.desc()).limit(RECENT_MAIL))).all()
    return _page(request, user, "mail", logs=logs, checks=mail_check(settings))
