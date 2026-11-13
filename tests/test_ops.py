"""운영·신뢰성: 요청 id·보안 헤더·CSP, /health, 오류 화면, DB 고정창 속도 제한."""

import json
import logging

import httpx
import pytest

from app.core import ratelimit
from app.core.logging import JsonFormatter
from app.main import app
from tests.conftest import csrf_of, signup


def upstream(request: httpx.Request) -> httpx.Response:
    return httpx.Response(404)


# --- 헤더 ---


async def test_request_id_echoed_and_security_headers(api):
    async with api(upstream) as c:
        r = await c.get("/health", headers={"x-vercel-id": "icn1::abc-123"})
        r2 = await c.get("/health")
    assert r.headers["x-request-id"] == "icn1::abc-123"
    assert len(r2.headers["x-request-id"]) == 32  # uuid4 hex
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    csp = r.headers["content-security-policy-report-only"]
    assert "default-src 'self'" in csp and "cdn.jsdelivr.net" in csp and "fonts.gstatic.com" in csp
    assert "frame-ancestors 'none'" in csp
    assert "strict-transport-security" not in r.headers  # cookie_secure=false 인 테스트 기본값


# --- /health ---


async def test_health_reports_db(api):
    async with api(upstream) as c:
        r = await c.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "db": "ok", "version": "0.1.0"}


async def test_health_503_when_db_down(api):
    class BrokenSession:
        async def execute(self, *_):
            raise RuntimeError("db down")

    from app.db.session import get_session

    async def broken():
        yield BrokenSession()

    async with api(upstream) as c:
        app.dependency_overrides[get_session] = broken
        r = await c.get("/health")
    assert r.status_code == 503
    assert r.json()["db"] == "error" and r.json()["status"] == "degraded"


# --- 오류 화면 ---


async def test_404_page_in_app_design(api):
    async with api(upstream) as c:
        r = await c.get("/no-such-page", headers={"accept": "text/html"})
        j = await c.get("/api/v1/books/999999", headers={"accept": "application/json"})
    assert r.status_code == 404 and "text/html" in r.headers["content-type"]
    assert "그런 쪽은 없어요" in r.text and 'href="/"' in r.text
    assert j.status_code == 404 and j.headers["content-type"].startswith("application/json")


async def test_500_page_hides_exception(api):
    secret = "SUPER-SECRET-EXCEPTION-TEXT"

    @app.get("/_boom")
    async def boom():
        raise RuntimeError(secret)

    try:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/_boom", headers={"accept": "text/html"})
    finally:
        app.router.routes[:] = [rt for rt in app.router.routes if getattr(rt, "path", "") != "/_boom"]
    assert r.status_code == 500
    assert "잠시 문제가 생겼어요" in r.text and 'href="/"' in r.text
    assert secret not in r.text and "RuntimeError" not in r.text


# --- 로그 ---


def test_json_formatter_fields():
    rec = logging.LogRecord("haedok.access", logging.INFO, __file__, 1, "request", (), None)
    rec.request_id, rec.path, rec.status, rec.duration_ms = "rid-1", "/x", 200, 3.2
    line = json.loads(JsonFormatter().format(rec))
    assert line["request_id"] == "rid-1" and line["path"] == "/x" and line["status"] == 200
    assert line["duration_ms"] == 3.2 and line["level"] == "INFO" and line["logger"] == "haedok.access"
    assert line["ts"].endswith("+00:00") and line["msg"] == "request"


# --- 속도 제한 ---


async def test_fixed_window_counts_and_rolls_over(session_factory, monkeypatch):
    now = {"t": 1_700_000_000.0}
    monkeypatch.setattr(ratelimit, "_now", lambda: now["t"])
    async with session_factory() as s:
        counts = [await ratelimit.hit(s, "k", 3, 600) for _ in range(4)]
        assert counts == [1, 2, 3, 4]
        with pytest.raises(ratelimit.RateLimited) as exc:
            await ratelimit.check(s, "k", 3, 600, "너무 많아요")
        assert exc.value.message == "너무 많아요" and 0 < exc.value.retry_after <= 600
        now["t"] += 600  # 다음 창
        assert await ratelimit.hit(s, "k", 3, 600) == 1
        assert await ratelimit.hit(s, "other", 3, 600) == 1  # 키마다 따로


async def test_login_429_after_ten_attempts_per_email_and_ip(api):
    async with api(upstream) as c:
        await signup(c, email="limit@example.com")
        c.cookies.clear()
        token = await csrf_of(c, "/login")
        data = {"email": "limit@example.com", "password": "wrong-pass", "csrf": token}
        statuses = [(await c.post("/login", data=data)).status_code for _ in range(10)]
        assert statuses == [200] * 10
        blocked = await c.post("/login", data=data)
        other_ip = await c.post("/login", data=data, headers={"x-forwarded-for": "203.0.113.9"})
    assert blocked.status_code == 429 and "10분 뒤에" in blocked.text
    assert other_ip.status_code == 200  # 다른 IP는 따로 센다


async def test_signup_429_per_email(api):
    async with api(upstream) as c:
        token = await csrf_of(c)
        form = {
            "nickname": "독자", "email": "spam@example.com", "password": "password123",
            "password_confirm": "password123", "csrf": token,
        }  # fmt: skip
        for _ in range(5):
            r = await c.post("/signup", data=form)
            assert r.status_code == 303
        r = await c.post("/signup", data=form)
    assert r.status_code == 429 and "너무 자주 요청했어요" in r.text
