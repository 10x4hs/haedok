"""받는 사람에게 메일이 안 나가면(예: Resend 테스트 모드는 본인 주소로만 보낸다)
관리자 주소로 코드를 대신 보낸다. 관리자가 코드를 전해 주면 가입을 마칠 수 있다."""

import json

import httpx
import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models import MailLog
from app.services import mail
from app.services.mail import send_password_reset_code as real_reset
from app.services.mail import send_signup_code as real_send
from tests.conftest import mock_client
from tests.test_signup_verify import csrf_of, upstream

OWNER = "owner@example.com"


@pytest.fixture
def resend_test_mode(monkeypatch):
    """Resend 도메인 검증 전: 관리자 주소로만 보내지고 나머지는 403."""
    for k in ("SMTP_USER", "SMTP_PASSWORD", "BREVO_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setenv("VERCEL_ENV", "production")
    monkeypatch.setenv("ADMIN_EMAILS", OWNER)
    get_settings.cache_clear()
    sent: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        if body["to"] != [OWNER]:
            return httpx.Response(
                403, json={"message": "You can only send testing emails to your own email address"}
            )
        sent.append(body)
        return httpx.Response(200, json={"id": "x"})

    yield handler, sent
    get_settings.cache_clear()


async def test_signup_code_relayed_to_admin(resend_test_mode, session_factory):
    handler, sent = resend_test_mode
    async with mock_client(handler) as c, session_factory() as s:
        result = await real_send(c, "friend@example.com", "123456", session=s)
    assert result == mail.SENT_ADMIN
    assert len(sent) == 1
    assert "friend@example.com" in sent[0]["subject"] and "123456" in sent[0]["subject"]
    async with session_factory() as s:
        logged = list(await s.scalars(select(MailLog).order_by(MailLog.id)))
    assert [(r.kind, r.to_email, r.ok) for r in logged] == [
        ("signup_code", "friend@example.com", False),
        ("signup_code_relay", OWNER, True),
    ]


async def test_direct_send_still_reports_user(resend_test_mode):
    handler, sent = resend_test_mode
    async with mock_client(handler) as c:
        assert await real_send(c, OWNER, "123456") == mail.SENT_USER
    assert len(sent) == 1 and "관리자" not in sent[0]["subject"]


async def test_reset_code_relayed_to_admin(resend_test_mode):
    handler, sent = resend_test_mode
    async with mock_client(handler) as c:
        assert await real_reset(c, "friend@example.com", "654321") == mail.SENT_ADMIN
    assert "654321" in sent[0]["subject"]


async def test_no_admin_means_failure(resend_test_mode, monkeypatch):
    handler, sent = resend_test_mode
    monkeypatch.setenv("ADMIN_EMAILS", "")
    get_settings.cache_clear()
    async with mock_client(handler) as c:
        assert not await real_send(c, "friend@example.com", "123456")
    assert sent == []


async def test_verify_screen_says_code_went_to_admin(api, monkeypatch):
    """관리자에게 대신 갔으면 인증 화면이 그렇게 알려 준다."""

    async def relayed(client, email, code, session=None):
        return mail.SENT_ADMIN

    monkeypatch.setattr(mail, "send_signup_code", relayed)
    async with api(upstream) as c:
        token = await csrf_of(c)
        form = {
            "nickname": "친구",
            "email": "friend@example.com",
            "password": "password123",
            "password_confirm": "password123",
            "csrf": token,
        }
        r = await c.post("/signup", data=form)
        assert r.status_code == 303 and r.headers["location"] == "/signup/verify"
        assert "관리자 메일로 보냈어요" in (await c.get("/signup/verify")).text
