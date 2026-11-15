"""메일 전송 수단: SMTP 465 → 587 → Resend 순서, 배포에서는 Resend 우선, 시도마다 mail_logs 한 줄."""

import json
import smtplib

import httpx
import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models import MailLog
from app.services import mail
from app.services.mail import (
    send_signup_code as real_send,  # conftest가 발송 함수를 가짜로 바꾸기 전에 잡아 둔다
)
from tests.conftest import mock_client


@pytest.fixture
def env(monkeypatch):
    """SMTP·Resend·Vercel 설정을 바꾸고 끝나면 캐시를 비운다."""

    def set_(**kw):
        for k, v in kw.items():
            if v is None:
                monkeypatch.delenv(k, raising=False)
            else:
                monkeypatch.setenv(k, v)
        get_settings.cache_clear()

    set_(SMTP_USER=None, SMTP_PASSWORD=None, RESEND_API_KEY=None, BREVO_API_KEY=None, VERCEL_ENV=None)
    yield set_
    get_settings.cache_clear()


async def rows(session_factory) -> list[MailLog]:
    async with session_factory() as s:
        return list(await s.scalars(select(MailLog).order_by(MailLog.id)))


async def test_smtp_used_when_configured(env, monkeypatch):
    env(SMTP_USER="me@gmail.com", SMTP_PASSWORD="app-pass")
    sent = {}
    monkeypatch.setattr(
        mail, "_send_smtp", lambda s, port, to, subj, text, html: sent.update(port=port, to=to)
    )
    async with httpx.AsyncClient() as c:
        assert await real_send(c, "a@b.com", "123456")
    assert sent == {"port": 465, "to": "a@b.com"}


async def test_smtp_auth_failure_skips_other_port(env, monkeypatch, session_factory):
    env(SMTP_USER="me@gmail.com", SMTP_PASSWORD="bad")
    ports = []

    def boom(settings, port, *a):
        ports.append(port)
        raise smtplib.SMTPAuthenticationError(535, b"bad")

    monkeypatch.setattr(mail, "_send_smtp", boom)
    async with session_factory() as s, httpx.AsyncClient() as c:
        assert not await real_send(c, "a@b.com", "123456", session=s)
    assert ports == [465, 587]  # 587도 시도는 하되(다른 수단 없음) 결과는 실패
    logged = await rows(session_factory)
    assert [(r.transport, r.ok) for r in logged] == [("smtp465", False), ("smtp587", False)]
    assert "SMTPAuthenticationError" in logged[0].error and "123456" not in (logged[0].error or "")


async def test_smtp_465_drop_falls_back_to_587_then_resend(env, monkeypatch, session_factory):
    """운영에서 본 그대로: 465·587 모두 'Connection unexpectedly closed' → Resend로 나간다."""
    env(SMTP_USER="me@gmail.com", SMTP_PASSWORD="abcd efgh ijkl mnop", RESEND_API_KEY="re_x")

    def dead(settings, port, *a):
        raise smtplib.SMTPServerDisconnected("Connection unexpectedly closed")

    monkeypatch.setattr(mail, "_send_smtp", dead)
    posted = []
    client = mock_client(lambda req: posted.append(req) or httpx.Response(200, json={"id": "x"}))
    async with session_factory() as s:
        assert await real_send(client, "a@b.com", "123456", session=s)
    assert len(posted) == 1
    logged = await rows(session_factory)
    assert [(r.transport, r.ok) for r in logged] == [("smtp465", False), ("smtp587", False), ("resend", True)]
    assert all(r.kind == "signup_code" and r.to_email == "a@b.com" for r in logged)
    assert all(r.duration_ms is not None and r.created_at is not None for r in logged)


async def test_brevo_first_on_vercel_and_payload_shape(env, session_factory):
    """도메인 없이도 가는 Brevo를 배포에서 가장 먼저 쓴다. 발신자는 BREVO_FROM 을 이름/주소로 나눈다."""
    env(
        BREVO_API_KEY="xkeysib-x",
        BREVO_FROM="해독 <me@gmail.com>",
        RESEND_API_KEY="re_x",
        VERCEL_ENV="production",
    )
    assert mail.transports(get_settings()) == ["brevo", "resend"]
    posted = []
    client = mock_client(lambda req: posted.append(req) or httpx.Response(201, json={"messageId": "x"}))
    async with session_factory() as s:
        assert await real_send(client, "a@b.com", "123456", session=s)
    assert len(posted) == 1 and str(posted[0].url) == mail.BREVO_URL
    assert posted[0].headers["api-key"] == "xkeysib-x"
    body = json.loads(posted[0].content)
    assert body["sender"] == {"name": "해독", "email": "me@gmail.com"} and body["to"] == [
        {"email": "a@b.com"}
    ]
    assert "123456" in body["subject"] and "123456" in body["htmlContent"]
    logged = await rows(session_factory)
    assert [(r.transport, r.ok) for r in logged] == [("brevo", True)]


async def test_brevo_error_falls_through_to_resend(env, session_factory):
    env(BREVO_API_KEY="xkeysib-x", RESEND_API_KEY="re_x", VERCEL_ENV="production")

    def route(req):
        if "brevo" in str(req.url):
            return httpx.Response(401, json={"message": "Key not found"})
        return httpx.Response(200, json={"id": "x"})

    async with session_factory() as s:
        assert await real_send(mock_client(route), "a@b.com", "123456", session=s)
    logged = await rows(session_factory)
    assert [(r.transport, r.ok) for r in logged] == [("brevo", False), ("resend", True)]
    assert "Brevo 401" in logged[0].error


async def test_vercel_prefers_resend_over_smtp(env, monkeypatch):
    env(SMTP_USER="me@gmail.com", SMTP_PASSWORD="x", RESEND_API_KEY="re_x", VERCEL_ENV="production")
    assert mail.transports(get_settings()) == ["resend", "smtp465", "smtp587"]
    env(VERCEL_ENV=None)
    assert mail.transports(get_settings()) == ["smtp465", "smtp587", "resend"]
    env(SMTP_USER=None, RESEND_API_KEY=None)
    assert mail.transports(get_settings()) == ["log"]


async def test_no_mail_config_on_vercel_is_a_failure(env, session_factory, caplog):
    """배포에서 메일 설정이 비면 '보냈다'고 속이지 않는다 — 코드도 로그에 남기지 않는다."""
    env(VERCEL_ENV="production")
    client = mock_client(lambda req: httpx.Response(500))
    async with session_factory() as s:
        assert not await real_send(client, "a@b.com", "123456", session=s)
    logged = await rows(session_factory)
    assert [(r.transport, r.ok) for r in logged] == [("log", False)]
    assert "메일 설정 없음" in logged[0].error and "123456" not in caplog.text


async def test_no_mail_config_locally_logs_the_code(env, session_factory, caplog):
    client = mock_client(lambda req: httpx.Response(500))
    async with session_factory() as s:
        assert await real_send(client, "a@b.com", "123456", session=s)
    assert "123456" in caplog.text
    logged = await rows(session_factory)
    assert [(r.transport, r.ok, r.error) for r in logged] == [("log", True, None)]


def test_send_smtp_uses_ssl_on_465_and_starttls_on_587(monkeypatch):
    from types import SimpleNamespace

    calls = []

    class Fake:
        def __init__(self, host, port, **kw):
            calls.append((port, kw.get("timeout")))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def ehlo(self):
            calls.append("ehlo")

        def starttls(self, **kw):
            calls.append("starttls")

        def login(self, u, p):
            assert (u, p) == ("me@gmail.com", "abcdefghijklmnop")  # 앱 비밀번호의 공백은 지운다

        def send_message(self, m):
            calls.append("sent")

    monkeypatch.setattr(smtplib, "SMTP_SSL", Fake)
    monkeypatch.setattr(smtplib, "SMTP", Fake)
    st = SimpleNamespace(
        smtp_host="smtp.gmail.com",
        smtp_port=465,
        smtp_user="me@gmail.com",
        smtp_password="abcd efgh ijkl mnop",
    )
    mail._send_smtp(st, 465, "a@b.com", "s", "t", "<p>h</p>")
    mail._send_smtp(st, 587, "a@b.com", "s", "t", "<p>h</p>")
    assert calls == [
        (465, mail.SMTP_TIMEOUT_S),
        "sent",
        (587, mail.SMTP_TIMEOUT_S),
        "ehlo",
        "starttls",
        "ehlo",
        "sent",
    ]
