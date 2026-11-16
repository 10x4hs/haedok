import time

from app import auth
from tests.conftest import csrf_of, signup
from tests.test_api import upstream


def form(**kw):
    base = {
        "nickname": "독자",
        "email": "a@b.co",
        "password": "password123",
        "password_confirm": "password123",
    }
    return auth.SignupForm(**(base | kw))


def test_signup_validation_rules():
    assert form().validate()
    f = form(email="  Reader@Example.COM ")
    assert f.validate() and f.email == "reader@example.com"
    assert "password" in (f := form(password="short", password_confirm="short"), f.validate(), f.errors)[2]
    assert (
        "password" in (f := form(password="가" * 25, password_confirm="가" * 25), f.validate(), f.errors)[2]
    )  # 75바이트
    assert "password_confirm" in (f := form(password_confirm="different1"), f.validate(), f.errors)[2]
    assert "email" in (f := form(email="nope"), f.validate(), f.errors)[2]


def test_password_hash_roundtrip():
    h = auth.hash_password("password123")
    assert h.startswith("$2b$12$")
    assert auth.verify_password("password123", h)
    assert not auth.verify_password("password124", h)
    assert not auth.verify_password("x", None)


async def test_duplicate_email_is_rejected_case_insensitively(api):
    async with api(upstream) as c, api(upstream) as other:
        await signup(c, email="dup@example.com")
        token = await csrf_of(other)
        r = await other.post(
            "/signup",
            data={
                "nickname": "둘째",
                "email": "DUP@example.com",
                "password": "password123",
                "password_confirm": "password123",
                "csrf": token,
            },
        )
    assert r.status_code == 200 and "이미 가입된 이메일" in r.text
    assert "password123" not in r.text  # 비밀번호를 다시 출력하지 않는다


async def test_login_logout_and_wrong_password(api):
    async with api(upstream) as c:
        await signup(c, email="me@example.com")
        c.cookies.clear()
        token = await csrf_of(c, "/login")
        bad = await c.post(
            "/login", data={"email": "me@example.com", "password": "wrongpass1", "csrf": token}
        )
        ok = await c.post(
            "/login",
            data={"email": " ME@example.com", "password": "password123", "next": "/library", "csrf": token},
        )
    assert bad.status_code == 200 and "맞지 않아요" in bad.text
    assert ok.status_code == 303 and ok.headers["location"] == "/library"


async def test_login_rejects_offsite_next(api):
    async with api(upstream) as c:
        await signup(c, email="me@example.com")
        c.cookies.clear()
        token = await csrf_of(c, "/login")
        r = await c.post(
            "/login",
            data={
                "email": "me@example.com",
                "password": "password123",
                "next": "//evil.example",
                "csrf": token,
            },
        )
    assert r.headers["location"] == "/"


async def test_post_without_csrf_is_forbidden(api):
    async with api(upstream) as c:
        r = await c.post(
            "/signup",
            data={
                "nickname": "a",
                "email": "x@y.z",
                "password": "password123",
                "password_confirm": "password123",
            },
        )
    assert r.status_code == 403


async def test_session_expires_after_idle(api, monkeypatch):
    async with api(upstream) as c:
        await signup(c)
        assert (await c.get("/me")).status_code == 200
        real = time.time
        monkeypatch.setattr(auth.time, "time", lambda: real() + 31 * 60)
        r = await c.get("/me")
    assert r.status_code == 303


# --- 로그인·가입 화면 개선 ---


async def login_as(c, email="me@example.com", password="password123", **extra):
    token = await csrf_of(c, "/login")
    return await c.post("/login", data={"email": email, "password": password, "csrf": token, **extra})


def session_cookie(r) -> str:
    return next(v for v in r.headers.get_list("set-cookie") if v.startswith("session="))


async def test_login_page_has_toggle_capslock_remember_and_links(api):
    async with api(upstream) as c:
        html = (await c.get("/login?next=/library")).text
    assert "data-toggle-pw" in html and 'aria-pressed="false"' in html  # 비밀번호 보기 버튼
    assert "data-caps" in html  # Caps Lock 경고 자리
    assert 'name="remember"' in html and "로그인 상태 유지" in html
    assert 'href="/password/forgot"' in html and "비밀번호를 잊었어요" in html
    assert 'href="/signup' in html
    assert 'name="next" value="/library"' in html
    assert "aria-live" in html


async def test_login_wrong_password_is_inline_field_error(api):
    async with api(upstream) as c:
        await signup(c, email="me@example.com")
        c.cookies.clear()
        r = await login_as(c, password="wrongpass1")
    assert r.status_code == 200
    assert 'id="password-err"' in r.text and "맞지 않아요" in r.text
    assert 'aria-invalid="true"' in r.text
    assert 'value="me@example.com"' in r.text  # 이메일은 다시 채운다


async def test_login_without_remember_keeps_short_cookie(api):
    async with api(upstream) as c:
        await signup(c, email="me@example.com")
        c.cookies.clear()
        r = await login_as(c)
    assert r.status_code == 303
    assert "Max-Age=1800;" in session_cookie(r)


async def test_login_remember_me_extends_cookie_and_idle_window(api, monkeypatch):
    async with api(upstream) as c:
        await signup(c, email="me@example.com")
        c.cookies.clear()
        r = await login_as(c, remember="1")
        assert f"Max-Age={auth.REMEMBER_S};" in session_cookie(r)
        real = time.time
        monkeypatch.setattr(auth.time, "time", lambda: real() + 3 * 24 * 3600)  # 사흘 뒤
        assert (await c.get("/me")).status_code == 200


async def test_login_rate_limit_shows_clear_message(api):
    async with api(upstream) as c:
        await signup(c, email="me@example.com")
        c.cookies.clear()
        for _ in range(10):
            await login_as(c, password="wrongpass1")
        r = await login_as(c, next="/library")
    assert r.status_code == 429
    assert "너무 여러 번" in r.text and "10분" in r.text
    assert 'name="next" value="/library"' in r.text


async def test_login_next_survives_error_and_rejects_absolute(api):
    async with api(upstream) as c:
        html = (await c.get("/login?next=https://evil.example/x")).text
        assert 'name="next" value="/"' in html
        await signup(c, email="me@example.com")
        c.cookies.clear()
        r = await login_as(c, password="nope-nope", next="/books/3")
    assert 'name="next" value="/books/3"' in r.text


async def test_signup_page_has_live_validation_hooks_and_terms(api):
    async with api(upstream) as c:
        html = (await c.get("/signup")).text
    for name in ("nickname", "email", "password", "password_confirm"):
        assert f'id="{name}-err"' in html
    assert "data-strength" in html  # 비밀번호 강도
    assert "이용약관" in html and "개인정보" in html
    assert "data-toggle-pw" in html


async def test_signup_server_errors_map_to_fields(api):
    async with api(upstream) as c:
        token = await csrf_of(c)
        r = await c.post(
            "/signup",
            data={
                "nickname": "",
                "email": "nope",
                "password": "short",
                "password_confirm": "other",
                "csrf": token,
            },
        )
    assert r.status_code == 200
    import re

    def err(name):
        m = re.search(rf'id="{name}-err"[^>]*>([^<]*)<', r.text)
        return m.group(1) if m else ""

    assert "1~40자" in err("nickname")
    assert "이메일" in err("email")
    assert "8자" in err("password")
    assert "달라요" in err("password_confirm")
