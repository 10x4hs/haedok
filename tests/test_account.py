"""내 정보 › 계정: 비밀번호 바꾸기 · 다른 기기 모두 로그아웃 · 계정 삭제."""

from sqlalchemy import func, select, text

from app.models import LibraryEntry, User
from tests.conftest import csrf_of, signup
from tests.test_api import upstream

EMAIL = "me@example.com"


async def two_devices(a, b) -> None:
    await signup(a, email=EMAIL)
    token = await csrf_of(b, "/login")
    r = await b.post("/login", data={"email": EMAIL, "password": "password123", "csrf": token})
    assert r.status_code == 303


async def test_me_shows_account_section_with_nonce(api):
    async with api(upstream) as c:
        await signup(c, email=EMAIL)
        html = (await c.get("/me")).text
    assert 'action="/me/password"' in html and 'action="/me/logout-all"' in html
    assert 'action="/me/delete"' in html and 'name="confirm_email"' in html
    assert '<script nonce="' in html


async def test_change_password_needs_current_and_logs_out_others(api):
    async with api(upstream) as a, api(upstream) as b:
        await two_devices(a, b)
        token = await csrf_of(a, "/me")
        bad = await a.post(
            "/me/password",
            data={
                "current": "wrongpass1",
                "password": "newpass456",
                "password_confirm": "newpass456",
                "csrf": token,
            },
        )
        assert bad.status_code == 303 and "error=" in bad.headers["location"]
        assert (await b.get("/me")).status_code == 200
        ok = await a.post(
            "/me/password",
            data={
                "current": "password123",
                "password": "newpass456",
                "password_confirm": "newpass456",
                "csrf": token,
            },
        )
        assert ok.status_code == 303 and "account=pw" in ok.headers["location"]
        assert (await a.get("/me")).status_code == 200  # 바꾼 기기는 그대로
        assert (await b.get("/me")).status_code == 303  # 다른 기기는 로그아웃
        b.cookies.clear()
        token = await csrf_of(b, "/login")
        r = await b.post("/login", data={"email": EMAIL, "password": "newpass456", "csrf": token})
    assert r.status_code == 303


async def test_change_password_validates_new_password(api):
    async with api(upstream) as c:
        await signup(c, email=EMAIL)
        token = await csrf_of(c, "/me")
        r = await c.post(
            "/me/password",
            data={"current": "password123", "password": "short", "password_confirm": "short", "csrf": token},
        )
    assert "error=" in r.headers["location"]


async def test_logout_everywhere_else(api):
    async with api(upstream) as a, api(upstream) as b:
        await two_devices(a, b)
        token = await csrf_of(a, "/me")
        r = await a.post("/me/logout-all", data={"csrf": token})
        assert r.status_code == 303 and "account=out" in r.headers["location"]
        assert (await a.get("/me")).status_code == 200
        assert (await b.get("/me")).status_code == 303


async def test_account_endpoints_need_csrf(api):
    async with api(upstream) as c:
        await signup(c, email=EMAIL)
        for path, data in [
            (
                "/me/password",
                {"current": "password123", "password": "newpass456", "password_confirm": "newpass456"},
            ),
            ("/me/logout-all", {}),
            ("/me/delete", {"confirm_email": EMAIL}),
        ]:
            assert (await c.post(path, data=data)).status_code == 403


async def test_delete_account_requires_typed_email(api, session_factory):
    async with api(upstream) as c:
        await signup(c, email=EMAIL)
        token = await csrf_of(c, "/me")
        r = await c.post("/me/delete", data={"confirm_email": "other@example.com", "csrf": token})
        assert "error=" in r.headers["location"]
    async with session_factory() as s:
        assert await s.scalar(select(func.count(User.id))) == 1


async def test_delete_account_removes_user_and_data(api, session_factory):
    async with api(upstream) as c:
        await signup(c, email=EMAIL)
        token = await csrf_of(c, "/me")
        # 서재 데이터 하나 만든다
        h = {"X-CSRF-Token": token}
        r = await c.post(
            "/api/v1/books/import", json={"source": "wikisource", "external_id": "긴글"}, headers=h
        )
        await c.put(f"/api/v1/books/{r.json()['id']}/progress", json={"chunk_index": 0}, headers=h)
        async with session_factory() as s:
            assert await s.scalar(select(func.count(LibraryEntry.id))) == 1
            # 운영(Postgres)처럼 FK CASCADE를 켠다 — StaticPool이라 이 연결 하나를 모두가 쓴다
            await s.execute(text("PRAGMA foreign_keys=ON"))
        r = await c.post("/me/delete", data={"confirm_email": " ME@example.com ", "csrf": token})
        assert r.status_code == 303 and r.headers["location"].startswith("/")
        assert (await c.get("/me")).status_code == 303  # 로그아웃됐다
    async with session_factory() as s:
        assert await s.scalar(select(func.count(User.id))) == 0
        assert await s.scalar(select(func.count(LibraryEntry.id))) == 0
