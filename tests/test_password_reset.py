"""비밀번호 재설정: 메일 6자리 코드(가입 코드와 같은 규칙) → 새 비밀번호 → 다른 기기 로그아웃.
가입 여부는 응답으로 드러나지 않는다."""

import time

import httpx
import pytest
from sqlalchemy import select

from app import auth
from app.models import User
from app.services import mail
from tests.conftest import csrf_of, signup
from tests.test_api import upstream

EMAIL = "me@example.com"
RESET_CODES: dict[str, str] = {}


@pytest.fixture(autouse=True)
def _capture_reset_mail(monkeypatch):
    async def fake(client, email, code, session=None):
        RESET_CODES[email] = code
        return True

    RESET_CODES.clear()
    monkeypatch.setattr(mail, "send_password_reset_code", fake)
    yield
    RESET_CODES.clear()


def shift_clock(monkeypatch, seconds: float) -> None:
    real = time.time
    monkeypatch.setattr(auth.time, "time", lambda: real() + seconds)


async def forgot(c: httpx.AsyncClient, email: str) -> httpx.Response:
    token = await csrf_of(c, "/password/forgot")
    return await c.post("/password/forgot", data={"email": email, "csrf": token})


async def reset(c: httpx.AsyncClient, code: str, password: str = "newpass456", confirm: str | None = None):
    token = await csrf_of(c, "/password/reset")
    return await c.post(
        "/password/reset",
        data={"code": code, "password": password, "password_confirm": confirm or password, "csrf": token},
    )


def wrong(code: str) -> str:
    return f"{(int(code) + 1) % 10**6:06d}"


async def test_forgot_page_renders(api):
    async with api(upstream) as c:
        r = await c.get("/password/forgot")
    assert r.status_code == 200 and 'name="email"' in r.text and "비밀번호" in r.text


async def test_unknown_email_gets_the_same_response(api):
    async with api(upstream) as c:
        await signup(c, email=EMAIL)
        c.cookies.clear()
        known = await forgot(c, EMAIL)
        c.cookies.clear()
        unknown = await forgot(c, "ghost@example.com")
        assert known.status_code == unknown.status_code == 303
        assert known.headers["location"] == unknown.headers["location"] == "/password/reset"
        page_unknown = (await c.get("/password/reset")).text
    assert RESET_CODES.keys() == {EMAIL}  # 없는 이메일로는 메일이 나가지 않는다
    assert "ghost@example.com" in page_unknown and "가입된 계정이 있으면" in page_unknown


async def test_unknown_email_wrong_code_looks_like_wrong_code(api):
    async with api(upstream) as c:
        await forgot(c, "ghost@example.com")
        r = await reset(c, "123456")
    assert r.status_code == 200 and "코드가 맞지 않아요" in r.text


async def test_wrong_code_counts_attempts_and_locks(api):
    async with api(upstream) as c:
        await signup(c, email=EMAIL)
        c.cookies.clear()
        await forgot(c, EMAIL)
        code = RESET_CODES[EMAIL]
        r = await reset(c, wrong(code))
        assert "코드가 맞지 않아요" in r.text and "남은 기회 4번" in r.text
        for _ in range(4):
            r = await reset(c, wrong(code))
        assert "너무 많이 틀렸어요" in r.text
        r = await reset(c, code)  # 맞는 코드여도 잠긴 뒤에는 안 된다
    assert "너무 많이 틀렸어요" in r.text


async def test_expired_code_is_rejected(api, monkeypatch):
    async with api(upstream) as c:
        await signup(c, email=EMAIL)
        c.cookies.clear()
        await forgot(c, EMAIL)
        shift_clock(monkeypatch, auth.CODE_TTL_S + 5)
        r = await reset(c, RESET_CODES[EMAIL])
    assert "만료" in r.text


async def test_weak_new_password_does_not_use_an_attempt(api, session_factory):
    async with api(upstream) as c:
        await signup(c, email=EMAIL)
        c.cookies.clear()
        await forgot(c, EMAIL)
        r = await reset(c, wrong(RESET_CODES[EMAIL]), password="short")
    assert 'id="password-err"' in r.text and "8자" in r.text
    assert "남은 기회" not in r.text


async def test_success_changes_password_and_logs_out_other_sessions(api, session_factory):
    async with api(upstream) as other, api(upstream) as c:
        await signup(other, email=EMAIL)
        assert (await other.get("/me")).status_code == 200  # 다른 기기에서 로그인 중
        await forgot(c, EMAIL)
        r = await reset(c, RESET_CODES[EMAIL])
        assert r.status_code == 303
        assert (await c.get("/me")).status_code == 200  # 재설정한 기기는 바로 로그인된다
        assert (await other.get("/me")).status_code == 303  # 다른 기기는 로그아웃
        c.cookies.clear()
        token = await csrf_of(c, "/login")
        old = await c.post("/login", data={"email": EMAIL, "password": "password123", "csrf": token})
        new = await c.post("/login", data={"email": EMAIL, "password": "newpass456", "csrf": token})
    assert old.status_code == 200 and new.status_code == 303
    # 코드는 한 번만 쓸 수 있다
    async with session_factory() as s:
        assert await auth.pending_reset(s, EMAIL) is None
        assert (await s.scalar(select(User).where(User.email == EMAIL))).session_version >= 1


async def test_forgot_is_rate_limited(api):
    async with api(upstream) as c:
        statuses = [(await forgot(c, "ghost@example.com")).status_code for _ in range(6)]
    assert statuses[:5] == [303] * 5 and statuses[5] == 429


async def test_reset_page_without_request_goes_back_to_forgot(api):
    async with api(upstream) as c:
        r = await c.get("/password/reset")
    assert r.status_code == 303 and r.headers["location"] == "/password/forgot"


async def test_resend_within_cooldown_does_not_send_new_code(api):
    async with api(upstream) as c:
        await signup(c, email=EMAIL)
        c.cookies.clear()
        await forgot(c, EMAIL)
        first = RESET_CODES[EMAIL]
        await forgot(c, EMAIL)
    assert RESET_CODES[EMAIL] == first
