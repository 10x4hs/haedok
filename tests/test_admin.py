"""관리자 화면 — ADMIN_EMAILS에 적힌 사람만 열고, 나머지는 404."""

import datetime as dt

import httpx
import pytest
from sqlalchemy import select

from app import auth
from app.core.config import get_settings
from app.models import MailLog, SignupVerification, User
from tests.conftest import csrf_of, signup


def upstream(_: httpx.Request) -> httpx.Response:
    return httpx.Response(404)


@pytest.fixture
def admin_emails(monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_emails", "Admin@Example.com, other@example.com")


async def test_anonymous_gets_404(api, admin_emails):
    async with api(upstream) as c:
        for path in ("/admin", "/admin/users", "/admin/rooms", "/admin/mail"):
            assert (await c.get(path)).status_code == 404


async def test_normal_user_gets_404(api, admin_emails):
    async with api(upstream) as c:
        await signup(c, email="reader@example.com")
        assert (await c.get("/admin")).status_code == 404
        assert "/admin" not in (await c.get("/me")).text


async def test_nobody_is_admin_when_setting_is_empty(api, monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_emails", "")
    async with api(upstream) as c:
        await signup(c, email="admin@example.com")
        assert (await c.get("/admin")).status_code == 404


async def test_admin_sees_every_page(api, admin_emails, session_factory):
    async with api(upstream) as c:
        await signup(c, email="admin@example.com")
        async with session_factory() as s:
            s.add(
                MailLog(
                    to_email="x@example.com", kind="signup_code", ok=False, transport="resend", error="boom"
                )
            )
            s.add(
                MailLog(
                    to_email="y@example.com",
                    kind="signup_code",
                    ok=True,
                    transport="smtp465",
                    duration_ms=120,
                )
            )
            await s.commit()
        home = await c.get("/admin")
        assert home.status_code == 200 and "가입 대기" in home.text
        assert "1 <small>/ 1</small>" in home.text  # 24시간 메일 성공 / 실패
        assert 'href="/admin"' in (await c.get("/me")).text
        users = await c.get("/admin/users")
        assert users.status_code == 200 and "admin@example.com" in users.text and "관리자" in users.text
        assert (await c.get("/admin/rooms")).status_code == 200
        mail = await c.get("/admin/mail")
        assert mail.status_code == 200 and "boom" in mail.text and "로그에만 남김" in mail.text
        assert "dev-only-change-me" not in mail.text


async def test_user_search_by_email(api, admin_emails):
    async with api(upstream) as c:
        await signup(c, email="admin@example.com")
        c.cookies.clear()
        await signup(c, email="zed@example.com", nickname="제드")
        c.cookies.clear()
        token = await csrf_of(c, "/login")
        await c.post("/login", data={"email": "admin@example.com", "password": "password123", "csrf": token})
        page = await c.get("/admin/users", params={"q": "ZED"})
        assert "zed@example.com" in page.text and "제드" in page.text
        assert "admin@example.com" not in page.text.split("가입한 사용자")[1]


async def test_pending_signup_can_be_approved_or_deleted(api, admin_emails, session_factory):
    async with api(upstream) as c:
        await signup(c, email="admin@example.com")
        token = await csrf_of(c, "/me")
        now = int(dt.datetime.now(dt.UTC).timestamp())
        pw = auth.hash_password("secret-pw-1")
        async with session_factory() as s:
            s.add_all(
                [
                    SignupVerification(
                        email="a@example.com",
                        nickname="가",
                        password_hash=pw,
                        code_hash="h",
                        expires_at=now + 60,
                        sent_at=now,
                    ),
                    SignupVerification(
                        email="b@example.com",
                        nickname="나",
                        password_hash=pw,
                        code_hash="h",
                        expires_at=now - 60,
                        sent_at=now - 700,
                    ),
                ]
            )
            await s.commit()
            ids = dict((await s.execute(select(SignupVerification.email, SignupVerification.id))).all())
        page = await c.get("/admin/users")
        assert "a@example.com" in page.text and "만료됨" in page.text

        r = await c.post(f"/admin/signups/{ids['a@example.com']}/approve", data={"csrf": token})
        assert r.status_code == 303 and "/admin/users?msg=" in r.headers["location"]
        r = await c.post(f"/admin/signups/{ids['b@example.com']}/delete", data={"csrf": token})
        assert r.status_code == 303
        async with session_factory() as s:
            assert (await s.scalar(select(User).where(User.email == "a@example.com"))) is not None
            assert (await s.scalar(select(SignupVerification))) is None

        # 승인된 사람은 바로 로그인된다
        c.cookies.clear()
        token = await csrf_of(c, "/login")
        r = await c.post("/login", data={"email": "a@example.com", "password": "secret-pw-1", "csrf": token})
        assert r.status_code == 303 and r.headers["location"] == "/"


async def test_delete_user_requires_confirm_and_not_self(api, admin_emails, session_factory):
    async with api(upstream) as c:
        await signup(c, email="victim@example.com")
        c.cookies.clear()
        await signup(c, email="admin@example.com")
        token = await csrf_of(c, "/me")
        async with session_factory() as s:
            ids = dict((await s.execute(select(User.email, User.id))).all())

        r = await c.post(f"/admin/users/{ids['victim@example.com']}/delete", data={"csrf": token})
        assert "err=" in r.headers["location"]
        r = await c.post(
            f"/admin/users/{ids['admin@example.com']}/delete", data={"csrf": token, "confirm": "1"}
        )
        assert "err=" in r.headers["location"]
        r = await c.post(
            f"/admin/users/{ids['victim@example.com']}/delete", data={"csrf": token, "confirm": "1"}
        )
        assert r.status_code == 303 and "msg=" in r.headers["location"]
        async with session_factory() as s:
            assert (await s.scalar(select(User).where(User.email == "victim@example.com"))) is None


async def test_admin_posts_need_csrf(api, admin_emails):
    async with api(upstream) as c:
        await signup(c, email="admin@example.com")
        assert (await c.post("/admin/signups/1/delete", data={})).status_code == 403
