"""웹 앱 화면 — 소개 · 가입/로그인 · 홈 · 탐색 · 서재 · 검색 · 올리기 · 내 정보 · 리더.

서버 렌더링(Jinja2)이 기본이고, 리더만 작은 JS가 붙는다.
"""

import asyncio
import datetime as dt
import hashlib
import json
import random
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import auth, seo
from app.api.deps import get_http_client, get_sources
from app.core import appearance, ratelimit
from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.models import Book, LibraryEntry, Room, Shelf, User
from app.services import bestsellers, bookstore, mail, social
from app.services import board as board_service
from app.services import books as book_service
from app.services import points as points_service
from app.services import races as race_service
from app.services.search import FULLTEXT_SOURCES, search_all
from app.services.uploads import parse_upload
from app.sources.base import BookSource, SourceError

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

# 서가 카탈로그 — scripts/build_catalog.py로 만든다 (퍼블릭 도메인 확인된 작품만)
DATA = Path(__file__).parent / "data"
STATIC = Path(__file__).parent / "static"


def _load_catalog() -> list[dict]:
    """같은 작품이 여러 번(판본·문서명 차이) 실려 있으면 하나만 남기고, 표지 파일이 없는 환경(배포)에서는
    원본 표지 주소를 쓴다."""
    seen, out = set(), []
    for b in json.loads((DATA / "catalog.json").read_text()):
        key = (b["title"].strip().lower(), (b.get("author") or "").strip().lower())
        if key in seen:
            continue
        seen.add(key)
        if b.get("cover") and not (STATIC / b["cover"].removeprefix("/static/")).exists():
            b["cover"] = b.get("cover_src")
        out.append(b)
    return out


CATALOG: list[dict] = _load_catalog()
# 저작권이 살아 있는 유명 도서 — 본문 없이 정보·표지와 알라딘 전자책 구매 링크만 (scripts/build_store.py)
STORE: list[dict] = json.loads((DATA / "store.json").read_text())
STORE_INDEX = {b["id"]: b for b in STORE}
# 책 소개 유튜브 영상 — scripts/build_videos.py ("store:<알라딘 id>" 또는 "<source>:<id>" → 영상 목록)
VIDEOS: dict[str, list[dict]] = (
    json.loads((DATA / "videos.json").read_text()) if (DATA / "videos.json").exists() else {}
)
GENRE_ORDER = [
    "단편소설", "장편소설", "중편소설", "고전소설", "신소설", "판소리", "시", "시집", "희곡", "동화", "수필",
    "세계 고전", "세계 · 추리", "세계 · SF", "세계 · 판타지", "세계 · 동화", "세계 · 모험", "세계 · 공포",
    "세계 · 로맨스", "세계 · 시", "세계 · 희곡", "세계 · 철학", "세계 · 어린이", "세계 · 역사",
]  # fmt: skip
SHELVES = {g: [b for b in CATALOG if b["genre"] == g] for g in GENRE_ORDER}
SHELVES = {g: books for g, books in SHELVES.items() if books}
CATALOG_INDEX = {(b["source"], b["id"]): b for b in CATALOG}
POPULAR_ORDER = [tuple(k) for k in json.loads((DATA / "popular.json").read_text())["order"]]


def _load_showcase() -> list[dict]:
    """첫 화면 띠에 거는 유명한 책들(app/data/showcase.json). 앱에 본문이 있는 책만 읽기 링크를 달고,
    나머지는 표지만 보여 준다. 표지 파일이 없으면 원본 주소를 쓴다."""
    out = []
    for b in json.loads((DATA / "showcase.json").read_text()):
        b = dict(b)
        b["readable"] = bool(b.get("readable")) and (b.get("source"), b.get("id")) in CATALOG_INDEX
        if not (STATIC / b["cover"].removeprefix("/static/")).exists():
            b["cover"] = b["cover_src"]
        out.append(b)
    return out


SHOWCASE: list[dict] = _load_showcase()

COVER_PALETTES = 8


def cover(title: str, author: str | None = None) -> dict:
    """표지가 없는 고전에 쓸 결정적 표지 — 제목 해시로 색과 무늬를 고른다."""
    h = int(hashlib.sha1(f"{title}|{author or ''}".encode()).hexdigest(), 16)
    return {"palette": h % COVER_PALETTES, "pattern": (h >> 8) % 4}


def cover_of(book: Book) -> str | None:
    """책의 실제 표지: 올릴 때 고른 서점 표지 → 카탈로그 표지 순."""
    if book.cover_url:
        return book.cover_url
    meta = CATALOG_INDEX.get((book.source, book.external_id))
    return meta.get("cover") if meta else None


def small_cover(url: str | None) -> str | None:
    """같은 표지의 작은 판(알라딘 cover200, Open Library -M) — 쌓인 책처럼 작게 보일 때 쓴다."""
    if not url:
        return url
    return url.replace("/cover500/", "/cover200/").replace("-L.jpg", "-M.jpg")


templates.env.filters["small_cover"] = small_cover
templates.env.globals["cover"] = cover
templates.env.globals["cover_of"] = cover_of
templates.env.globals["APPEARANCE"] = appearance
seo.install(templates.env)  # canonical·OG 주소


def _render(
    request: Request, name: str, user: User | None, status_code: int = 200, **ctx
) -> HTMLResponse:
    look = appearance.resolve(user.settings if user else None)
    ctx |= {"user": user, "csrf": auth.csrf_token(request), "path": request.url.path, "look": look}
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def _safe_next(target: str | None) -> str:
    """로그인 뒤 돌아갈 곳 — 이 사이트 안의 경로만.

    `//evil.com`과 `/\\evil.com`(브라우저가 \\를 /로 바꾼다)은 다른 사이트로 가므로 막는다.
    """
    if not target or not target.startswith("/") or "\\" in target or target.startswith("//"):
        return "/"
    parts = urlsplit(target)
    return target if not parts.scheme and not parts.netloc else "/"


def _daily_picks(n: int) -> list[dict]:
    rng = random.Random(dt.date.today().toordinal())
    with_cover = [b for b in CATALOG if b.get("cover")] or CATALOG
    return rng.sample(with_cover, min(n, len(with_cover)))


async def popular(session: AsyncSession, n: int = 20) -> list[dict]:
    """유명 고전 순서에 앱 안 독자 수를 더한 인기 순위."""
    rows = await session.execute(
        select(Book.source, Book.external_id, func.count(LibraryEntry.id))
        .join(LibraryEntry, LibraryEntry.book_id == Book.id)
        .where(Book.owner_id.is_(None))
        .group_by(Book.source, Book.external_id)
    )
    readers = {(src, ext): cnt for src, ext, cnt in rows.all()}
    base = len(POPULAR_ORDER)
    score = {key: base - i for i, key in enumerate(POPULAR_ORDER)}
    for key, cnt in readers.items():
        score[key] = score.get(key, 0) + cnt * 3
    ranked = sorted((k for k in score if k in CATALOG_INDEX), key=lambda k: -score[k])[:n]
    return [CATALOG_INDEX[k] | {"rank": i + 1, "readers": readers.get(k, 0)} for i, k in enumerate(ranked)]


def _fill(ranked: list[dict], n: int) -> list[dict]:
    """인기 순위 뒤를 오늘의 추천으로 채워 드럼 한 바퀴를 꽉 채운다."""
    seen = {(b["source"], b["id"]) for b in ranked}
    extra = [b for b in _daily_picks(n * 2) if (b["source"], b["id"]) not in seen]
    return (ranked + extra)[:n]


# 홈 나선에 거는 유명한 책(서점) — app/data/home_picks.json에 적힌 순서대로. 서점에 없는 id는 건너뛴다
HOME_PICKS: list[dict] = [
    STORE_INDEX[i] | {"store": True}
    for i in json.loads((DATA / "home_picks.json").read_text())["store"]
    if i in STORE_INDEX
]
HOME_FREE = 4  # 맨 뒤에 붙이는, 앱에서 바로 읽는 고전 수 — 나선 앞쪽은 모두 유명한 책이다
HOME_BEST = 3  # 맨 앞에 거는 지금의 베스트셀러 수
# 지금 많이 팔리는 책(app/data/bestsellers.json — 하루 한 번 갱신, app/services/bestsellers.py)
BESTSELLERS: list[dict] = bestsellers.load()
BEST_INDEX = {b["id"]: b for b in BESTSELLERS}


def _same_book(title: str) -> str:
    """판 이름·부제를 떼고 견준다: '소년이 온다 (개정판)' == '소년이 온다'."""
    return re.sub(r"\s*[(\[:\-–—].*$", "", title).replace(" ", "").lower()


def _drum(free: list[dict], best: list[dict] | None = None) -> list[dict]:
    """맨 앞에 분야별 베스트셀러 1위 세 권(소설·인문·에세이), 그 뒤로 유명한 책, 맨 뒤에 바로 읽는 고전."""
    top = [
        b
        | {"store": True, "rank": None}
        | {"best": f"{b.get('category') or '베스트셀러'} {b.get('rank') or n}위"}
        for n, b in enumerate(bestsellers.leaders(best or [], HOME_BEST), 1)
    ]
    taken = {_same_book(b["title"]) for b in top}
    picks = [b for b in HOME_PICKS if _same_book(b["title"]) not in taken]  # 같은 책이 두 번 걸리지 않게
    return top + picks + list(free)


# --- 소개 · 가입 · 로그인 ---


@router.get("/", response_class=HTMLResponse)
async def home(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User | None = Depends(auth.current_user),
) -> HTMLResponse:
    if user is None:
        picks = await popular(session, 9)
        return _render(request, "landing.html", None, picks=picks, showcase=SHOWCASE, total=len(CATALOG))
    top = await popular(session, HOME_FREE)
    return _render(
        request,
        "home.html",
        user,
        drum=_drum(_fill(top, HOME_FREE), BESTSELLERS),
        first_run=bool(request.session.get("first_run")),
    )


@router.post("/welcome/dismiss")
async def welcome_dismiss(
    request: Request, csrf: str = Form(""), user: User = Depends(auth.require_user)
) -> RedirectResponse:
    """첫 방문 안내 카드를 닫는다 — 세션 표시만 지우므로 스키마 변경이 없다."""
    auth.check_csrf(request, csrf)
    request.session.pop("first_run", None)
    return RedirectResponse("/", status_code=303)


@router.get("/signup", response_class=HTMLResponse)
async def signup_form(
    request: Request, next: str | None = None, user: User | None = Depends(auth.current_user)
) -> HTMLResponse:
    if user:
        return RedirectResponse(_safe_next(next), status_code=303)
    if next:
        # 초대 링크(/join/CODE)에서 온 사람은 가입·인증을 마친 뒤 그곳으로 돌려보낸다 (세션에만 둔다)
        request.session["signup_next"] = _safe_next(next)
    return _render(request, "signup.html", None, form=None)


@router.post("/signup", response_class=HTMLResponse, response_model=None)
async def signup(
    request: Request,
    nickname: str = Form(""),
    email: str = Form(""),
    password: str = Form(""),
    password_confirm: str = Form(""),
    csrf: str = Form(""),
    session: AsyncSession = Depends(get_session),
    http: httpx.AsyncClient = Depends(get_http_client),
) -> HTMLResponse | RedirectResponse:
    auth.check_csrf(request, csrf)
    form = auth.SignupForm(nickname, email, password, password_confirm)
    if form.validate():
        try:
            await ratelimit.check_signup(session, form.email, ratelimit.client_ip(request))
        except ratelimit.RateLimited as e:
            form.errors["email"] = e.message
            return _render(request, "signup.html", None, form=form, status_code=429)
        if await auth.email_taken(session, form.email):
            form.errors["email"] = "이미 가입된 이메일이에요"
        else:
            code = await auth.start_signup(session, form)
            sent = (
                True if code is None else await mail.send_signup_code(http, form.email, code, session=session)
            )
            if sent:
                request.session["signup_email"] = form.email
                request.session["signup_relayed"] = sent == mail.SENT_ADMIN
                return RedirectResponse("/signup/verify", status_code=303)
            # 안 나간 메일은 재발송 간격을 풀어 둔다.
            # 안 그러면 60초 안의 재시도가 메일 없이 조용히 인증 화면으로 넘어간다
            await auth.mail_failed(session, form.email)
            form.errors["email"] = MAIL_FAILED
    # 비밀번호는 다시 채워 보내지 않는다
    return _render(request, "signup.html", None, form=form)


MAIL_FAILED = (
    "인증 메일을 보내지 못했어요. 가입 요청은 접수됐으니 관리자가 확인하면 "
    "바로 로그인할 수 있어요. 급하면 잠시 뒤 다시 시도해 주세요"
)

# Resend 도메인 검증 전에는 관리자 주소로만 메일이 나가서, 코드를 관리자에게 대신 보낸다
RELAYED = "인증 코드를 관리자 메일로 보냈어요. 관리자에게 코드를 받아 입력해 주세요"


def _code_notice(request: Request) -> str:
    return RELAYED if request.session.get("signup_relayed") else "메일로 받은 6자리 코드를 입력해 주세요"


async def _verify_page(
    request: Request,
    session: AsyncSession,
    email: str,
    error: str | None = None,
    notice: str | None = None,
    kind: str = "",
):
    """인증 화면. 남은 기회·만료·재발송 대기를 함께 내려 화면이 상태를 보여 주게 한다."""
    pending = await auth.pending_signup(session, email)
    now = int(time.time())
    expires_in = max(0, pending.expires_at - now) if pending else 0
    attempts_left = max(0, auth.MAX_CODE_ATTEMPTS - pending.attempts) if pending else 0
    resend_wait = max(0, auth.RESEND_COOLDOWN_S - (now - pending.sent_at)) if pending else 0
    if error and not kind:
        kind = "expired" if expires_in == 0 else "locked" if attempts_left == 0 else "wrong"
    return _render(
        request,
        "signup_verify.html",
        None,
        email=email,
        error=error,
        notice=notice,
        kind=kind,
        expires_in=expires_in,
        attempts_left=attempts_left,
        resend_wait=resend_wait,
        locked=kind in ("expired", "locked"),
    )


@router.get("/signup/verify", response_class=HTMLResponse, response_model=None)
async def signup_verify_form(
    request: Request, session: AsyncSession = Depends(get_session)
) -> HTMLResponse | RedirectResponse:
    email = request.session.get("signup_email")
    if not email:
        return RedirectResponse("/signup", status_code=303)
    return await _verify_page(request, session, email, notice=_code_notice(request))


@router.post("/signup/verify", response_class=HTMLResponse, response_model=None)
async def signup_verify(
    request: Request,
    code: str = Form(""),
    csrf: str = Form(""),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse | RedirectResponse:
    auth.check_csrf(request, csrf)
    email = request.session.get("signup_email")
    if not email:
        return RedirectResponse("/signup", status_code=303)
    user, error = await auth.verify_signup(session, email, code)
    if user is None:
        return await _verify_page(request, session, email, error=error)
    target = _safe_next(request.session.get("signup_next"))
    auth.login(request, user)  # 세션을 새로 만들며 signup_email·signup_next도 지워진다
    request.session["first_run"] = True  # 홈의 첫 방문 안내 카드 (닫으면 지워진다)
    return RedirectResponse(target, status_code=303)


@router.post("/signup/resend", response_class=HTMLResponse, response_model=None)
async def signup_resend(
    request: Request,
    csrf: str = Form(""),
    session: AsyncSession = Depends(get_session),
    http: httpx.AsyncClient = Depends(get_http_client),
) -> HTMLResponse | RedirectResponse:
    auth.check_csrf(request, csrf)
    email = request.session.get("signup_email")
    if not email:
        return RedirectResponse("/signup", status_code=303)
    try:
        await ratelimit.check_signup(session, email, ratelimit.client_ip(request))
    except ratelimit.RateLimited as e:
        page = await _verify_page(request, session, email, error=e.message, kind="wait")
        page.status_code = 429
        return page
    code, wait = await auth.resend_code(session, email)
    if wait < 0:
        return RedirectResponse("/signup", status_code=303)
    if code is None:
        return await _verify_page(
            request, session, email, error=f"{wait}초 뒤에 다시 받을 수 있어요", kind="wait"
        )
    sent = await mail.send_signup_code(http, email, code, session=session)
    if not sent:
        await auth.mail_failed(session, email)
        return await _verify_page(request, session, email, error=MAIL_FAILED, kind="mail")
    request.session["signup_relayed"] = sent == mail.SENT_ADMIN
    notice = RELAYED if sent == mail.SENT_ADMIN else "새 코드를 보냈어요"
    return await _verify_page(request, session, email, notice=notice)


def _login_page(
    request: Request,
    email: str = "",
    next: str | None = "/",
    errors: dict[str, str] | None = None,
    alert: str | None = None,
    remember: bool = False,
    status_code: int = 200,
) -> HTMLResponse:
    return _render(
        request,
        "login.html",
        None,
        email=email,
        errors=errors or {},
        alert=alert,
        next=_safe_next(next),
        remember=remember,
        status_code=status_code,
    )


@router.get("/login", response_class=HTMLResponse)
async def login_form(
    request: Request, next: str | None = None, user: User | None = Depends(auth.current_user)
) -> HTMLResponse:
    if user:
        return RedirectResponse(_safe_next(next), status_code=303)
    return _login_page(request, next=next)


@router.post("/login", response_class=HTMLResponse, response_model=None)
async def login(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    next: str = Form("/"),
    remember: str = Form(""),
    csrf: str = Form(""),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse | RedirectResponse:
    auth.check_csrf(request, csrf)
    page = {"email": auth.normalize_email(email), "next": next, "remember": bool(remember)}
    errors = {}
    if not page["email"]:
        errors["email"] = "이메일을 입력해 주세요"
    if not password:
        errors["password"] = "비밀번호를 입력해 주세요"
    if errors:
        return _login_page(request, errors=errors, **page)
    try:
        await ratelimit.check_login(session, email, ratelimit.client_ip(request))
    except ratelimit.RateLimited as e:
        return _login_page(request, alert=e.message, status_code=429, **page)
    user = await auth.authenticate(session, email, password)
    if user is None:
        return _login_page(request, errors={"password": "이메일 또는 비밀번호가 맞지 않아요"}, **page)
    auth.login(request, user, remember=bool(remember))
    return RedirectResponse(_safe_next(next), status_code=303)


@router.post("/logout")
async def logout(
    request: Request, csrf: str = Form(""), session: AsyncSession = Depends(get_session)
) -> RedirectResponse:
    auth.check_csrf(request, csrf)
    await auth.logout(request, session)
    return RedirectResponse("/", status_code=303)


# --- 비밀번호 재설정: 이메일 → 6자리 코드 + 새 비밀번호 ---


def _valid_email(email: str) -> bool:
    return "@" in email and not email.startswith("@") and len(email) <= 255


@router.get("/password/forgot", response_class=HTMLResponse)
async def password_forgot_form(request: Request, email: str = "") -> HTMLResponse:
    return _render(request, "password_forgot.html", None, email=auth.normalize_email(email), errors={})


@router.post("/password/forgot", response_class=HTMLResponse, response_model=None)
async def password_forgot(
    request: Request,
    email: str = Form(""),
    csrf: str = Form(""),
    session: AsyncSession = Depends(get_session),
    http: httpx.AsyncClient = Depends(get_http_client),
) -> HTMLResponse | RedirectResponse:
    auth.check_csrf(request, csrf)
    email = auth.normalize_email(email)
    if not _valid_email(email):
        errors = {"email": "올바른 이메일을 입력해 주세요"}
        return _render(request, "password_forgot.html", None, email=email, errors=errors)
    try:
        ip, window = ratelimit.client_ip(request), auth.RESET_WINDOW_S
        await ratelimit.check(session, f"reset-ip:{ip}", auth.RESET_IP_LIMIT, window, auth.RESET_LIMIT_MSG)
        await ratelimit.check(session, f"reset:{email}", auth.RESET_EMAIL_LIMIT, window, auth.RESET_LIMIT_MSG)
    except ratelimit.RateLimited as e:
        errors = {"email": e.message}
        return _render(request, "password_forgot.html", None, email=email, errors=errors, status_code=429)
    code = await auth.start_reset(session, email)
    # 가입 여부와 메일 성공 여부는 화면에 드러내지 않는다 (실패는 mail_logs에 남는다)
    if code is not None and not await mail.send_password_reset_code(http, email, code, session=session):
        await auth.reset_mail_failed(session, email)
    request.session["reset_email"] = email
    return RedirectResponse("/password/reset", status_code=303)


async def _reset_page(
    request: Request,
    session: AsyncSession,
    email: str,
    errors: dict[str, str] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    pending = await auth.pending_reset(session, email)
    now = int(time.time())
    expires_in = max(0, pending.expires_at - now) if pending else 0
    resend_wait = max(0, auth.RESEND_COOLDOWN_S - (now - pending.sent_at)) if pending else 0
    return _render(
        request,
        "password_reset.html",
        None,
        email=email,
        errors=errors or {},
        expires_in=expires_in,
        resend_wait=resend_wait,
        status_code=status_code,
    )


@router.get("/password/reset", response_class=HTMLResponse, response_model=None)
async def password_reset_form(
    request: Request, session: AsyncSession = Depends(get_session)
) -> HTMLResponse | RedirectResponse:
    email = request.session.get("reset_email")
    if not email:
        return RedirectResponse("/password/forgot", status_code=303)
    return await _reset_page(request, session, email)


@router.post("/password/reset", response_class=HTMLResponse, response_model=None)
async def password_reset(
    request: Request,
    code: str = Form(""),
    password: str = Form(""),
    password_confirm: str = Form(""),
    csrf: str = Form(""),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse | RedirectResponse:
    auth.check_csrf(request, csrf)
    email = request.session.get("reset_email")
    if not email:
        return RedirectResponse("/password/forgot", status_code=303)
    errors = auth.password_errors(password, password_confirm)
    if not code.strip().isdigit() or len(code.strip()) != 6:
        errors["code"] = "6자리 숫자를 입력해 주세요"
    if errors:  # 코드를 확인하기 전에 걸러, 비밀번호 실수로 기회를 쓰지 않게 한다
        return await _reset_page(request, session, email, errors)
    user, error = await auth.check_reset_code(session, email, code)
    if user is None:
        return await _reset_page(request, session, email, {"code": error or ""})
    await auth.finish_reset(session, user, password)
    auth.login(request, user, remember=False)  # 이 기기만 새로 로그인 (reset_email도 지워진다)
    return RedirectResponse("/me?account=reset#account", status_code=303)


# --- 내 정보 › 계정 ---

ACCOUNT_NOTICES = {
    "pw": "비밀번호를 바꿨어요. 다른 기기에서는 로그아웃됐어요",
    "out": "이 기기를 뺀 다른 기기에서 모두 로그아웃했어요",
    "reset": "새 비밀번호로 바꿨어요. 다른 기기에서는 로그아웃됐어요",
}
ACCOUNT_ERRORS = {
    "current": "현재 비밀번호가 맞지 않아요",
    "short": f"새 비밀번호는 {auth.MIN_PASSWORD_CHARS}자 이상이어야 해요",
    "long": "새 비밀번호가 너무 길어요 (최대 72바이트)",
    "match": "새 비밀번호가 서로 달라요",
    "limit": auth.PASSWORD_CHANGE_MSG,
    "confirm": "확인용 이메일이 계정 이메일과 달라요",
}


def _account_back(code: str, error: bool = False) -> RedirectResponse:
    key = "account_error" if error else "account"
    return RedirectResponse(f"/me?{key}={code}#account", status_code=303)


@router.post("/me/password")
async def change_password(
    request: Request,
    current: str = Form(""),
    password: str = Form(""),
    password_confirm: str = Form(""),
    csrf: str = Form(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> RedirectResponse:
    auth.check_csrf(request, csrf)
    try:
        key, limit = f"pwchange:{user.id}", auth.PASSWORD_CHANGE_LIMIT
        await ratelimit.check(session, key, limit, ratelimit.WINDOW_S, auth.PASSWORD_CHANGE_MSG)
    except ratelimit.RateLimited:
        return _account_back("limit", error=True)
    if not auth.verify_password(current, user.password_hash):
        return _account_back("current", error=True)
    errors = auth.password_errors(password, password_confirm)
    if "password" in errors:
        return _account_back("short" if len(password) < auth.MIN_PASSWORD_CHARS else "long", error=True)
    if errors:
        return _account_back("match", error=True)
    await auth.set_password(session, user, password)
    auth.login(request, user)  # 새 번호로 이 기기만 이어 간다
    return _account_back("pw")


@router.post("/me/logout-all")
async def logout_everywhere(
    request: Request,
    csrf: str = Form(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> RedirectResponse:
    auth.check_csrf(request, csrf)
    auth.bump_session_version(user)
    await session.commit()
    auth.login(request, user)
    return _account_back("out")


@router.post("/me/delete")
async def delete_my_account(
    request: Request,
    confirm_email: str = Form(""),
    csrf: str = Form(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> RedirectResponse:
    auth.check_csrf(request, csrf)
    if not user.email or auth.normalize_email(confirm_email) != user.email:
        return _account_back("confirm", error=True)
    await auth.delete_account(session, user)
    request.session.clear()
    return RedirectResponse("/", status_code=303)


# --- 앱 ---


@router.get("/discover", response_class=HTMLResponse)
async def discover(
    request: Request,
    g: str | None = None,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> HTMLResponse:
    """탐색: 이어 읽기 · 인기 순위 · 오늘의 추천 · 서점 · 장르별 선반. ?g= 를 주면 그 서가 전체 격자."""
    common = {"genres": SHELVES, "store": STORE, "total": len(CATALOG)}
    if g == "서점":
        return _render(request, "discover.html", user, genre="서점", books=[], **common)
    if g == "all" or g in SHELVES:
        genre = g if g in SHELVES else None
        return _render(
            request, "discover.html", user, genre=genre, books=SHELVES[genre] if genre else CATALOG, **common
        )
    library = await book_service.library_of(session, user.id)
    reading = [(e, b) for e, b in library if e.shelf == Shelf.READING]
    return _render(
        request,
        "explore.html",
        user,
        current=reading[0] if reading else None,
        reading=reading[1:6],
        picks=_daily_picks(10),
        marquee=_daily_picks(16),
        popular=await popular(session, 30),
        shelves={name: books[:14] for name, books in SHELVES.items()},
        **common,
    )


# 서점 실시간 조회(알라딘) 한도 — 사용자별, 5분 창. 캐시에 있는 것은 세지 않는다
STORE_WINDOW_S = 300
STORE_SEARCH_LIMIT = 30
STORE_LOOKUP_LIMIT = 60


async def _store_live_allowed(session: AsyncSession, user: User, kind: str, limit: int) -> bool:
    """한도 안이면 True. 한도를 셀 수 없을 때(DB 오류)도 화면은 살리고 알라딘에는 묻지 않는다."""
    try:
        await ratelimit.check(session, f"store-{kind}:{user.id}", limit, STORE_WINDOW_S, "")
    except ratelimit.RateLimited:
        return False
    except Exception:
        # 여기서 rollback하면 같은 세션의 user가 만료돼 화면을 그릴 수 없다 — 세션은 요청이 끝날 때 닫힌다
        bookstore.log.exception("bookstore rate limit check failed")
        return False
    return True


async def _store_search(
    session: AsyncSession, client: httpx.AsyncClient, user: User, term: str
) -> list[bookstore.StoreItem]:
    """알라딘 전자책 검색 결과. 캐시에 없을 때만 한도를 세고, 한도를 넘으면 빈 목록(고른 책만 보여 준다)."""
    cached = bookstore.cached_search(term)
    if cached is not None:
        return cached
    if not bookstore.live_enabled():
        return []
    if not await _store_live_allowed(session, user, "search", STORE_SEARCH_LIMIT):
        return []
    return await bookstore.search(client, term)


async def _store_book(
    session: AsyncSession, client: httpx.AsyncClient, user: User, item_id: str | None
) -> dict | None:
    """서점의 책 한 권: 고른 책 → (알라딘 ItemId 모양이면) 캐시 → 알라딘 상품 화면.

    없는 책이거나 한도를 넘으면 None."""
    item_id = item_id or ""
    if book := STORE_INDEX.get(item_id) or BEST_INDEX.get(item_id):
        return book
    if not bookstore.valid_id(item_id):
        return None
    hit, cached = bookstore.cached_lookup(item_id)
    if hit:
        return cached
    if not bookstore.live_enabled():
        return None
    if not await _store_live_allowed(session, user, "lookup", STORE_LOOKUP_LIMIT):
        return None
    return await bookstore.lookup(client, item_id)


@router.get("/store/{item_id}", response_class=HTMLResponse)
async def store_item(
    request: Request,
    item_id: str,
    session: AsyncSession = Depends(get_session),
    client: httpx.AsyncClient = Depends(get_http_client),
    user: User = Depends(auth.require_user),
) -> HTMLResponse:
    book = await _store_book(session, client, user, item_id)
    if book is None:
        raise HTTPException(404, "책을 찾을 수 없어요")
    return _render(request, "store.html", user, book=book, videos=VIDEOS.get(f"store:{item_id}", []))


@router.get("/shelf/{genre}")
async def shelf(genre: str) -> RedirectResponse:
    if genre not in SHELVES:
        raise HTTPException(404, "서가를 찾을 수 없어요")
    return RedirectResponse(f"/discover?g={genre}", status_code=301)


@router.get("/library", response_class=HTMLResponse)
async def library(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> HTMLResponse:
    entries = await book_service.library_of(session, user.id)
    tabs = {
        "reading": [x for x in entries if x[0].shelf == Shelf.READING],
        "done": [x for x in entries if x[0].shelf == Shelf.DONE],
        "mine": [x for x in entries if x[1].owner_id == user.id],
    }
    uploaded = await book_service.uploads_of(session, user.id)
    return _render(request, "library.html", user, tabs=tabs, uploaded=uploaded)


@router.get("/search", response_class=HTMLResponse)
async def search(
    request: Request,
    q: str = Query("", max_length=100),
    sources: dict[str, BookSource] = Depends(get_sources),
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_session),
    client: httpx.AsyncClient = Depends(get_http_client),
    user: User = Depends(auth.require_user),
) -> HTMLResponse:
    readable, others, errors, store_hits = [], [], {}, []
    term = q.strip()
    in_catalog = [b for b in CATALOG if term and (term in b["title"] or term in (b["author"] or ""))]
    if term:
        # 공개 도서관 소스와 서점(알라딘)을 함께 기다린다. 서점은 실패해도 []만 돌려주고 6초 안에 끝난다
        result, live = await asyncio.gather(
            search_all(list(sources.values()), term, 20, settings.http_timeout_seconds),
            _store_search(session, client, user, term),
        )
        store_hits = bookstore.merge(bookstore.local_search(STORE, term), live, STORE_INDEX)
        errors = result.errors
        known = {(b["source"], b["id"]) for b in in_catalog}
        for h in result.hits:
            if (h.meta.source, h.meta.external_id) in known:
                continue
            (readable if h.meta.has_fulltext else others).append(h.meta)
        # 서점 구역에 이미 나온 책(같은 ISBN)은 '정보만 있는 책'에 한 번 더 보이지 않게 한다
        in_store = {b["id"] for b in store_hits}
        others = [m for m in others if not (m.isbn13 and m.isbn13 in in_store)]
    return _render(
        request,
        "search.html",
        user,
        q=q,
        in_catalog=in_catalog[:30],
        readable=readable,
        store_hits=store_hits,
        others=others,
        errors=errors,
    )


@router.get("/upload", response_class=HTMLResponse)
async def upload_form(
    request: Request,
    store: str | None = None,
    session: AsyncSession = Depends(get_session),
    client: httpx.AsyncClient = Depends(get_http_client),
    user: User = Depends(auth.require_user),
) -> HTMLResponse:
    return _render(
        request,
        "upload.html",
        user,
        error=None,
        for_book=await _store_book(session, client, user, store),
        max_mb=get_settings().max_upload_mb,
    )


@router.post("/upload", response_class=HTMLResponse, response_model=None)
async def upload(
    request: Request,
    file: UploadFile = File(...),
    csrf: str = Form(""),
    store: str = Form(""),
    session: AsyncSession = Depends(get_session),
    client: httpx.AsyncClient = Depends(get_http_client),
    user: User = Depends(auth.require_user),
) -> HTMLResponse | RedirectResponse:
    auth.check_csrf(request, csrf)
    for_book = await _store_book(session, client, user, store)
    limit = get_settings().max_upload_mb * 1024 * 1024
    data = await file.read(limit + 1)
    try:
        parsed = parse_upload(file.filename or "untitled.txt", data, limit)
    except SourceError as e:
        resp = _render(
            request, "upload.html", user, error=str(e), for_book=for_book, max_mb=get_settings().max_upload_mb
        )
        resp.status_code = 422
        return resp
    book = await book_service.save_upload(session, parsed, user.id)
    if for_book and not book.cover_url:  # 서점에서 고른 책이면 그 표지를 붙인다
        book.cover_url = for_book.get("cover")
        await session.commit()
    return RedirectResponse(f"/books/{book.id}", status_code=303)


@router.get("/me", response_class=HTMLResponse)
async def me(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> HTMLResponse:
    entries = await book_service.library_of(session, user.id)
    stats = {
        "reading": sum(e.shelf == Shelf.READING for e, _ in entries),
        "done": sum(e.shelf == Shelf.DONE for e, _ in entries),
        "pages": sum(e.verified_chunk + 1 for e, _ in entries),
    }
    return _render(
        request,
        "me.html",
        user,
        stats=stats,
        points=await points_service.summary(session, user.id),
        error=request.query_params.get("error"),
        saved=bool(request.query_params.get("saved")),
        account_notice=ACCOUNT_NOTICES.get(request.query_params.get("account", "")),
        account_error=ACCOUNT_ERRORS.get(request.query_params.get("account_error", "")),
    )


@router.get("/leaderboard", response_class=HTMLResponse)
async def leaderboard(
    request: Request,
    range: str = "week",
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> HTMLResponse:
    board = await points_service.leaderboard(session, user.id, range)
    return _render(
        request,
        "leaderboard.html",
        user,
        board=board,
        mine=await points_service.summary(session, user.id),
        week_start=points_service.week_start(points_service.clock()).astimezone(points_service.KST),
    )


@router.get("/open/{source}/{external_id:path}")
async def open_book(
    source: str,
    external_id: str,
    sources: dict[str, BookSource] = Depends(get_sources),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> RedirectResponse:
    """퍼블릭 도메인 전문을 가져와(이미 있으면 캐시) 서재에 꽂고 리더로 보낸다."""
    src = sources.get(source)
    if src is None or source not in FULLTEXT_SOURCES:
        raise HTTPException(400, "이 책은 앱에서 본문을 제공하지 않아요")
    try:
        book = await book_service.import_book(session, src, external_id)
    except SourceError as e:
        raise HTTPException(422, f"본문을 가져오지 못했어요: {e}") from e
    except httpx.HTTPError as e:
        raise HTTPException(502, f"{source} 응답이 없어요 ({type(e).__name__})") from e
    meta = CATALOG_INDEX.get((source, external_id))
    if meta and not book.author and meta["author"]:
        book.author = meta["author"]  # 위키문헌 API는 저자를 주지 않아 카탈로그로 채운다
        await session.commit()
    return RedirectResponse(f"/books/{book.id}", status_code=303)


@router.get("/books/{book_id}", response_class=HTMLResponse)
async def reader(
    request: Request,
    book_id: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> HTMLResponse:
    book = await session.get(Book, book_id)
    if book is None or not book_service.can_read(book, user.id):
        raise HTTPException(404, "책을 찾을 수 없어요")
    entry = await book_service.shelve(session, user.id, book.id)
    videos = VIDEOS.get(f"{book.source}:{book.external_id}", [])
    return _render(request, "reader.html", user, book=book, entry=entry, videos=videos)


# --- 친구 대결 ---


@router.get("/races", response_class=HTMLResponse)
async def races(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> HTMLResponse:
    mine = await race_service.races_of(session, user.id)
    reading = [b for e, b in await book_service.library_of(session, user.id) if e.shelf == Shelf.READING]
    return _render(
        request,
        "races.html",
        user,
        races=[(r, b, race_service.is_over(r)) for r, b in mine],
        reading=reading,
        popular=await popular(session, 12),
        durations=race_service.DURATIONS,
        error=request.query_params.get("error"),
        together=await board_service.preview(session, user.id) if get_settings().feature_board else None,
    )


@router.post("/races")
async def create_race(
    request: Request,
    book_id: int = Form(...),
    days: int = Form(7),
    csrf: str = Form(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> RedirectResponse:
    auth.check_csrf(request, csrf)
    book = await session.get(Book, book_id)
    if book is None or not book_service.can_read(book, user.id) or book.owner_id is not None:
        raise HTTPException(400, "대결은 서가에 있는 책으로만 열 수 있어요")
    room = await race_service.create_race(session, user, book, days)
    return RedirectResponse(f"/races/{room.id}", status_code=303)


@router.post("/races/start/{source}/{external_id:path}")
async def create_race_from_catalog(
    request: Request,
    source: str,
    external_id: str,
    days: int = Form(7),
    csrf: str = Form(""),
    sources: dict[str, BookSource] = Depends(get_sources),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> RedirectResponse:
    """인기 고전을 골라 바로 대결 열기 — 아직 안 가져온 책이면 가져온다."""
    auth.check_csrf(request, csrf)
    src = sources.get(source)
    if src is None or source not in FULLTEXT_SOURCES:
        raise HTTPException(400, "이 책으로는 대결을 열 수 없어요")
    try:
        book = await book_service.import_book(session, src, external_id)
    except (SourceError, httpx.HTTPError) as e:
        raise HTTPException(502, f"책을 가져오지 못했어요 ({type(e).__name__})") from e
    room = await race_service.create_race(session, user, book, days)
    return RedirectResponse(f"/races/{room.id}", status_code=303)


@router.get("/races/{room_id}", response_class=HTMLResponse)
async def race(
    request: Request,
    room_id: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> HTMLResponse:

    room = await session.get(Room, room_id)
    if room is None or not await race_service.is_member(session, room, user.id):
        raise HTTPException(404, "대결을 찾을 수 없어요")
    book = await session.get(Book, room.book_id)
    board = await race_service.standings(session, room, user.id)
    invite = str(request.base_url).rstrip("/") + f"/join/{room.invite_code}"
    return _render(
        request,
        "race.html",
        user,
        room=room,
        book=book,
        board=board,
        invite=invite,
        over=race_service.is_over(room),
    )


@router.post("/join")
async def join_by_form(code: str = Form("")) -> RedirectResponse:
    return RedirectResponse(f"/join/{race_service.normalize_code(code)}", status_code=303)


@router.get("/join/{code}", response_class=HTMLResponse, response_model=None)
async def join_preview(
    request: Request,
    code: str,
    session: AsyncSession = Depends(get_session),
    user: User | None = Depends(auth.current_user),
) -> HTMLResponse | RedirectResponse:
    room = await race_service.by_code(session, code)
    if user is None:
        # 로그인 전 공개 미리보기 — 카카오·슬랙 봇이 OG 태그를 읽어 가고, 사람은 가입/로그인 뒤 돌아온다
        if room is None:
            return _render(request, "join_preview.html", None, room=None, noindex=True, status_code=404)
        book = await session.get(Book, room.book_id)
        owner = await session.get(User, room.owner_id)
        count = await race_service.member_count(session, room)
        return _render(
            request,
            "join_preview.html",
            None,
            room=room,
            book=book,
            owner=owner,
            count=count,
            next=f"/join/{room.invite_code}",
            noindex=True,
        )
    if room is None:
        return RedirectResponse("/races?error=코드를 찾을 수 없어요", status_code=303)
    if await race_service.is_member(session, room, user.id):
        return RedirectResponse(f"/races/{room.id}", status_code=303)
    book = await session.get(Book, room.book_id)
    owner = await session.get(User, room.owner_id)
    count = await race_service.member_count(session, room)
    return _render(request, "join.html", user, room=room, book=book, owner=owner, count=count)


@router.post("/join/{code}")
async def join(
    request: Request,
    code: str,
    csrf: str = Form(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> RedirectResponse:
    auth.check_csrf(request, csrf)
    try:
        await ratelimit.check_join(session, user.id)
    except ratelimit.RateLimited as e:
        return RedirectResponse(f"/races?error={e.message}", status_code=303)
    room = await race_service.by_code(session, code)
    if room is None:
        return RedirectResponse("/races?error=코드를 찾을 수 없어요", status_code=303)
    if reason := await race_service.join(session, room, user):
        return RedirectResponse(f"/races?error={reason}", status_code=303)
    await book_service.shelve(session, user.id, room.book_id)
    return RedirectResponse(f"/races/{room.id}", status_code=303)


# --- 프로필 ---

MAX_AVATAR_BYTES = 300_000


def avatar_url(user: User | None) -> str | None:
    return social.avatar_path(user.id, user.avatar) if user else None


templates.env.globals["avatar_url"] = avatar_url


@router.get("/avatars/{user_id}")
async def avatar(
    user_id: int, session: AsyncSession = Depends(get_session), _: User = Depends(auth.require_user)
):
    from fastapi.responses import Response

    u = await session.get(User, user_id)
    if u is None or not u.avatar or not await race_service.shares_room(session, _.id, user_id):
        raise HTTPException(404)  # 본인이나 같은 대결방 사람의 사진만
    return Response(
        u.avatar, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=31536000, immutable"}
    )


@router.post("/me/profile")
async def update_profile(
    request: Request,
    nickname: str = Form(""),
    photo: UploadFile | None = File(None),
    remove_photo: str = Form(""),
    csrf: str = Form(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> RedirectResponse:
    auth.check_csrf(request, csrf)
    name = nickname.strip()
    if not 1 <= len(name) <= 40:
        return RedirectResponse("/me?error=이름은 1~40자로 입력해 주세요", status_code=303)
    user.nickname = name
    if remove_photo:
        user.avatar = None
    elif photo is not None and photo.filename:
        data = await photo.read(MAX_AVATAR_BYTES + 1)
        # 브라우저가 줄여 보낸 JPEG만 받는다 (매직 넘버 확인)
        if len(data) > MAX_AVATAR_BYTES or not data.startswith(b"\xff\xd8\xff"):
            return RedirectResponse(
                "/me?error=사진을 읽지 못했어요. 다른 사진으로 해 주세요", status_code=303
            )
        user.avatar = data
    await session.commit()
    return RedirectResponse("/me?saved=1", status_code=303)
