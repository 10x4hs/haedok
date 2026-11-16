"""이메일·비밀번호 세션 인증 (이전 Spring Security 구현의 명세를 그대로 옮김).

- 이메일은 앞뒤 공백 제거 + 소문자로 저장·조회, DB UNIQUE로 동시 가입도 차단
- 비밀번호는 BCrypt(cost 12), 8자 이상 · UTF-8 72바이트 이하
- 세션은 서명된 HttpOnly·SameSite=Lax 쿠키, 30분 동안 요청이 없으면 만료
- 로그인 시 세션을 새로 만들고(고정 공격 방지), 모든 POST는 CSRF 토큰을 검사
- 가입은 이메일로 받은 6자리 코드를 확인한 뒤에야 계정이 생긴다 (10분 유효, 5번까지, 재발송 60초 간격)
- "로그인 상태 유지"를 고르면 쿠키·유휴 만료가 30일로 늘어난다
- 비밀번호 재설정도 같은 코드 규칙을 쓰고, 가입 여부는 응답으로 드러내지 않는다
"""

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass, field

import bcrypt
from fastapi import Depends, HTTPException, Request
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_session
from app.models import PasswordReset, Room, SignupVerification, Team, User

BCRYPT_COST = 12
MIN_PASSWORD_CHARS = 8
MAX_PASSWORD_BYTES = 72  # BCrypt가 이 이후를 버리므로 넘으면 거부
# 없는 이메일로 로그인해도 같은 시간이 걸리도록 비교에 쓰는 더미 해시
_DUMMY_HASH = bcrypt.hashpw(b"timing-equalizer", bcrypt.gensalt(BCRYPT_COST))


class LoginRequired(Exception):
    """페이지 요청에서 로그인이 필요할 때 — main.py에서 /login으로 보낸다."""


def normalize_email(email: str) -> str:
    return email.strip().lower()


def password_errors(password: str, confirm: str) -> dict[str, str]:
    """새 비밀번호 규칙 — 가입·재설정·변경 공통. 칸 이름(password, password_confirm) → 문구."""
    errors: dict[str, str] = {}
    if len(password) < MIN_PASSWORD_CHARS:
        errors["password"] = f"비밀번호는 {MIN_PASSWORD_CHARS}자 이상이어야 해요"
    elif len(password.encode()) > MAX_PASSWORD_BYTES:
        errors["password"] = "비밀번호가 너무 길어요 (최대 72바이트)"
    if password != confirm:
        errors["password_confirm"] = "비밀번호가 서로 달라요"
    return errors


@dataclass
class SignupForm:
    nickname: str
    email: str
    password: str
    password_confirm: str
    errors: dict[str, str] = field(default_factory=dict)

    def validate(self) -> bool:
        self.nickname = self.nickname.strip()
        self.email = normalize_email(self.email)
        if not 1 <= len(self.nickname) <= 40:
            self.errors["nickname"] = "이름은 1~40자로 입력해 주세요"
        if "@" not in self.email or len(self.email) > 255 or self.email.startswith("@"):
            self.errors["email"] = "올바른 이메일을 입력해 주세요"
        self.errors |= password_errors(self.password, self.password_confirm)
        return not self.errors


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(BCRYPT_COST)).decode()


def verify_password(password: str, hashed: str | None) -> bool:
    raw = password.encode()
    if hashed is None or len(raw) > MAX_PASSWORD_BYTES:
        bcrypt.checkpw(b"x", _DUMMY_HASH)
        return False
    return bcrypt.checkpw(raw, hashed.encode())


# --- 가입 이메일 인증 ---

CODE_TTL_S = 600
MAX_CODE_ATTEMPTS = 5
RESEND_COOLDOWN_S = 60


def new_code() -> str:
    return f"{secrets.randbelow(10**6):06d}"


def _code_hash(email: str, code: str, purpose: str = "") -> str:
    """purpose로 가입 코드와 재설정 코드를 갈라, 한쪽 코드를 다른 쪽에 쓰지 못하게 한다."""
    key = get_settings().secret_key.encode()
    msg = f"{purpose}:{email}:{code}" if purpose else f"{email}:{code}"
    return hmac.new(key, msg.encode(), hashlib.sha256).hexdigest()


async def email_taken(session: AsyncSession, email: str) -> bool:
    return bool(await session.scalar(select(User.id).where(User.email == normalize_email(email))))


async def pending_signup(session: AsyncSession, email: str) -> SignupVerification | None:
    return await session.scalar(
        select(SignupVerification).where(SignupVerification.email == normalize_email(email))
    )


async def start_signup(session: AsyncSession, form: SignupForm) -> str | None:
    """가입 대기를 만들거나 고친다. 새로 보낼 코드를 돌려주고, 재발송 간격 안이면 None(기존 코드 유지)."""
    now = int(time.time())
    pending = await pending_signup(session, form.email)
    if pending is not None and now - pending.sent_at < RESEND_COOLDOWN_S:
        pending.nickname, pending.password_hash = form.nickname, hash_password(form.password)
        await session.commit()
        return None
    code = new_code()
    if pending is None:
        pending = SignupVerification(email=form.email)
        session.add(pending)
    pending.nickname, pending.password_hash = form.nickname, hash_password(form.password)
    pending.code_hash, pending.attempts = _code_hash(form.email, code), 0
    pending.sent_at, pending.expires_at = now, now + CODE_TTL_S
    try:
        await session.commit()
    except IntegrityError:  # 같은 이메일로 동시에 두 번 눌렀을 때
        await session.rollback()
        return None
    return code


async def mail_failed(session: AsyncSession, email: str) -> None:
    """메일이 안 나갔으면 재발송 간격을 풀어, 바로 다시 눌러도 새 코드가 나가게 한다."""
    pending = await pending_signup(session, email)
    if pending is not None:
        pending.sent_at = 0
        await session.commit()


async def resend_code(session: AsyncSession, email: str) -> tuple[str | None, int]:
    """(새 코드, 0) 또는 아직 기다려야 하면 (None, 남은 초). 대기가 없으면 (None, -1)."""
    pending = await pending_signup(session, email)
    if pending is None:
        return None, -1
    now = int(time.time())
    wait = RESEND_COOLDOWN_S - (now - pending.sent_at)
    if wait > 0:
        return None, wait
    code = new_code()
    pending.code_hash, pending.attempts = _code_hash(pending.email, code), 0
    pending.sent_at, pending.expires_at = now, now + CODE_TTL_S
    await session.commit()
    return code, 0


async def verify_signup(session: AsyncSession, email: str, code: str) -> tuple[User | None, str | None]:
    """코드가 맞으면 계정을 만들어 (user, None), 아니면 (None, 오류 문구)."""
    pending = await pending_signup(session, email)
    if pending is None:
        return None, "인증 요청을 찾을 수 없어요. 처음부터 다시 가입해 주세요"
    if int(time.time()) > pending.expires_at:
        return None, "코드가 만료됐어요. 새 코드를 받아 주세요"
    if pending.attempts >= MAX_CODE_ATTEMPTS:
        return None, "코드를 너무 많이 틀렸어요. 새 코드를 받아 주세요"
    if not hmac.compare_digest(pending.code_hash, _code_hash(pending.email, code.strip())):
        pending.attempts += 1
        await session.commit()
        left = MAX_CODE_ATTEMPTS - pending.attempts
        if left <= 0:
            return None, "코드를 너무 많이 틀렸어요. 새 코드를 받아 주세요"
        return None, f"코드가 맞지 않아요 (남은 기회 {left}번)"
    user = User(nickname=pending.nickname, email=pending.email, password_hash=pending.password_hash)
    session.add(user)
    await session.delete(pending)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return None, "이미 가입된 이메일이에요"
    return user, None


# --- 비밀번호 재설정 ---
# 가입 코드와 같은 규칙(HMAC 저장 · CODE_TTL_S · MAX_CODE_ATTEMPTS · RESEND_COOLDOWN_S).
# 없는 이메일에도 대기 행을 만들어(코드는 보내지 않는다) 화면·문구·횟수가 똑같이 흘러가게 한다.

RESET_PURPOSE = "reset"
RESET_WINDOW_S = 600
RESET_EMAIL_LIMIT = 5
RESET_IP_LIMIT = 20
RESET_LIMIT_MSG = "재설정 메일을 너무 자주 요청했어요. 10분 뒤에 다시 해 주세요"
PASSWORD_CHANGE_LIMIT = 10
PASSWORD_CHANGE_MSG = "비밀번호 확인을 너무 여러 번 시도했어요. 10분 뒤에 다시 해 주세요"


async def pending_reset(session: AsyncSession, email: str) -> PasswordReset | None:
    return await session.scalar(select(PasswordReset).where(PasswordReset.email == normalize_email(email)))


async def start_reset(session: AsyncSession, email: str) -> str | None:
    """재설정 코드를 새로 만든다. 가입된 이메일이면 보낼 코드를, 아니면(또는 재발송 간격 안이면) None."""
    email, now = normalize_email(email), int(time.time())
    # 하루 넘게 지난 대기는 치운다 (없는 이메일 행이 쌓이지 않게)
    await session.execute(delete(PasswordReset).where(PasswordReset.expires_at < now - 86400))
    pending = await pending_reset(session, email)
    if pending is not None and now - pending.sent_at < RESEND_COOLDOWN_S:
        await session.commit()
        return None
    code = new_code()
    if pending is None:
        pending = PasswordReset(email=email)
        session.add(pending)
    pending.code_hash, pending.attempts = _code_hash(email, code, RESET_PURPOSE), 0
    pending.sent_at, pending.expires_at = now, now + CODE_TTL_S
    try:
        await session.commit()
    except IntegrityError:  # 같은 이메일로 동시에 두 번
        await session.rollback()
        return None
    return code if await email_taken(session, email) else None


async def reset_mail_failed(session: AsyncSession, email: str) -> None:
    pending = await pending_reset(session, email)
    if pending is not None:
        pending.sent_at = 0
        await session.commit()


async def check_reset_code(session: AsyncSession, email: str, code: str) -> tuple[User | None, str | None]:
    """코드가 맞으면 (user, None). 틀리면 (None, 문구) — 없는 이메일도 '틀린 코드'와 같게 흘러간다."""
    pending = await pending_reset(session, email)
    if pending is None:
        return None, "재설정 요청을 찾을 수 없어요. 처음부터 다시 해 주세요"
    if int(time.time()) > pending.expires_at:
        return None, "코드가 만료됐어요. 새 코드를 받아 주세요"
    if pending.attempts >= MAX_CODE_ATTEMPTS:
        return None, "코드를 너무 많이 틀렸어요. 새 코드를 받아 주세요"
    user = await session.scalar(select(User).where(User.email == pending.email))
    ok = hmac.compare_digest(pending.code_hash, _code_hash(pending.email, code.strip(), RESET_PURPOSE))
    if not ok or user is None:
        pending.attempts += 1
        await session.commit()
        left = MAX_CODE_ATTEMPTS - pending.attempts
        if left <= 0:
            return None, "코드를 너무 많이 틀렸어요. 새 코드를 받아 주세요"
        return None, f"코드가 맞지 않아요 (남은 기회 {left}번)"
    return user, None


async def finish_reset(session: AsyncSession, user: User, password: str) -> None:
    """새 비밀번호로 바꾸고, 다른 기기의 쿠키를 모두 무효로 만든 뒤 대기를 지운다."""
    await set_password(session, user, password, commit=False)
    await session.execute(delete(PasswordReset).where(PasswordReset.email == user.email))
    await session.commit()


# --- 계정 관리 ---


async def set_password(session: AsyncSession, user: User, password: str, commit: bool = True) -> None:
    user.password_hash = hash_password(password)
    bump_session_version(user)
    if commit:
        await session.commit()


def bump_session_version(user: User) -> None:
    """이 사용자의 모든 기존 쿠키를 무효로 만든다. 지금 기기는 login()을 다시 불러 이어 간다."""
    user.session_version = (user.session_version or 0) + 1


async def delete_account(session: AsyncSession, user: User) -> None:
    """관리자 삭제(app/admin.py delete_user)와 같은 순서로 지운다.
    rooms.owner_id · teams.created_by 는 CASCADE가 아니라 먼저 지우고, 나머지는 FK CASCADE."""
    for room in await session.scalars(select(Room).where(Room.owner_id == user.id)):
        await session.delete(room)
    for team in await session.scalars(select(Team).where(Team.created_by == user.id)):
        await session.delete(team)
    await session.delete(user)
    await session.commit()


# 로그인 횟수 제한은 app/core/ratelimit.py(DB 고정창)가 맡는다 — 서버리스 인스턴스 사이에도 공유된다


async def authenticate(session: AsyncSession, email: str, password: str) -> User | None:
    user = await session.scalar(select(User).where(User.email == normalize_email(email)))
    ok = verify_password(password, user.password_hash if user else None)
    return user if ok else None


# --- 세션 ---


REMEMBER_DAYS = 30
REMEMBER_S = REMEMBER_DAYS * 86400


def login(request: Request, user: User, remember: bool | None = None) -> None:
    """remember가 None이면 지금 세션의 '로그인 상태 유지' 선택을 그대로 이어 간다."""
    if remember is None:
        remember = bool(request.session.get("rm"))
    request.session.clear()  # 기존 세션 내용·CSRF 토큰을 버리고 새로 시작
    request.session.update(
        {
            "uid": user.id,
            "sv": user.session_version or 0,
            "seen": int(time.time()),
            "csrf": secrets.token_urlsafe(32),
        }
    )
    if remember:
        request.session["rm"] = 1


def idle_limit_s(session_data) -> int:
    return REMEMBER_S if session_data.get("rm") else get_settings().session_idle_minutes * 60


class SessionCookieAge:
    """세션 쿠키의 Max-Age를 세션마다 정한다.

    SessionMiddleware는 서명 유효기간과 쿠키 Max-Age에 같은 max_age(= REMEMBER_S)를 쓴다.
    '로그인 상태 유지'를 고르지 않은 세션은 이 미들웨어가 쿠키 Max-Age를 유휴 시간으로 줄인다.
    실제 만료는 서버의 session_uid()가 'seen' 시각으로 판단하므로 쿠키를 고쳐도 늘어나지 않는다.
    SessionMiddleware 바깥에 둬야 한다(main.py에서 그 뒤에 add_middleware).
    """

    def __init__(self, app, cookie: str = "session") -> None:
        self.app, self.prefix = app, f"{cookie}=".encode()

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message) -> None:
            if message["type"] == "http.response.start":
                data = scope.get("session") or {}
                short = f"Max-Age={idle_limit_s(data)};".encode()
                message["headers"] = [
                    (k, _set_max_age(v, short) if self._is_session_cookie(k, v) else v)
                    for k, v in message.get("headers", [])
                ]
            await send(message)

        await self.app(scope, receive, send_wrapper)

    def _is_session_cookie(self, key: bytes, value: bytes) -> bool:
        return key.lower() == b"set-cookie" and value.startswith(self.prefix)


def _set_max_age(cookie: bytes, value: bytes) -> bytes:
    start = cookie.find(b"Max-Age=")
    if start < 0:
        return cookie
    end = cookie.find(b";", start)
    return cookie[:start] + value + (cookie[end + 1 :] if end >= 0 else b"")


async def logout(request: Request, session: AsyncSession) -> None:
    """이 기기 쿠키를 지우고, 같은 계정의 다른(예전·탈취된) 쿠키도 더는 통하지 않게 번호를 올린다."""
    uid = request.session.get("uid")
    request.session.clear()
    if uid and (user := await session.get(User, uid)):
        bump_session_version(user)
        await session.commit()


def csrf_token(request: Request) -> str:
    if "csrf" not in request.session:
        request.session["csrf"] = secrets.token_urlsafe(32)
    return request.session["csrf"]


def check_csrf(request: Request, token: str | None) -> None:
    expected = request.session.get("csrf")
    if not expected or not token or not hmac.compare_digest(expected, token):
        raise HTTPException(403, "요청이 만료됐어요. 페이지를 새로고침해 주세요")


def session_uid(request) -> int | None:
    """세션의 사용자 id — 30분 넘게 쉬었으면 만료. HTTP 요청과 WebSocket 모두에 쓴다."""
    uid, seen = request.session.get("uid"), request.session.get("seen", 0)
    if uid is None:
        return None
    now = int(time.time())
    if now - seen > idle_limit_s(request.session):
        request.session.clear()
        return None
    request.session["seen"] = now  # 요청이 있을 때마다 만료 시각을 미룬다
    return uid


async def user_of_session(request, session: AsyncSession) -> User | None:
    uid = session_uid(request)
    user = await session.get(User, uid) if uid else None
    if user is not None and request.session.get("sv", 0) != (user.session_version or 0):
        request.session.clear()  # 로그아웃 이전에 받은 쿠키
        return None
    return user


async def current_user(request: Request, session: AsyncSession = Depends(get_session)) -> User | None:
    return await user_of_session(request, session)


async def require_user(user: User | None = Depends(current_user)) -> User:
    if user is None:
        raise LoginRequired
    return user


async def require_api_user(user: User | None = Depends(current_user)) -> User:
    if user is None:
        raise HTTPException(401, "로그인이 필요해요")
    return user
