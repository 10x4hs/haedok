"""메일 보내기 — Brevo·Resend HTTP API와 SMTP(Gmail 앱 비밀번호) 중 되는 쪽으로 보낸다.
둘 다 없으면 로컬에서는 코드를 로그에만 남기고, 배포(Vercel)에서는 실패로 처리한다.

Vercel 서버리스에서는 Gmail SMTP(465·587)가 바로 끊긴다(2026-11 운영 로그:
"SMTPServerDisconnected: Connection unexpectedly closed" 두 포트 모두). Vercel도 함수에서는
세션형 SMTP 대신 HTTP 메일 API를 권한다. 그래서 배포에서는 HTTP(Brevo → Resend)를 먼저 쓰고
SMTP는 예비로 둔다. Brevo는 도메인 없이 발신 주소 인증만으로 누구에게나 보낼 수 있어
Resend(도메인 검증 전엔 본인에게만)보다 앞에 둔다.
시도 하나하나를 mail_logs 에 남긴다(코드 원문은 절대 저장하지 않는다)."""

import asyncio
import logging
import smtplib
import ssl
import time
from dataclasses import dataclass
from email.message import EmailMessage

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models import MailLog

log = logging.getLogger("haedok.mail")
RESEND_URL = "https://api.resend.com/emails"
BREVO_URL = "https://api.brevo.com/v3/smtp/email"
# 서버리스 함수 예산(60초) 안에 465 → 587 → Resend 를 다 돌 수 있게 짧게 잡는다
SMTP_TIMEOUT_S = 10
RESEND_TIMEOUT_S = 10


@dataclass
class Attempt:
    transport: str  # brevo | resend | smtp465 | smtp587 | log
    ok: bool
    error: str | None = None
    duration_ms: int | None = None


async def send_signup_code(
    client: httpx.AsyncClient, email: str, code: str, session: AsyncSession | None = None
) -> bool:
    """가입 인증 코드를 보낸다. 받는 사람에게 갔다고 볼 수 있으면 True, 아니면 False."""
    subject = f"[해독] 가입 인증 코드 {code}"
    text = f"해독(偕讀) 가입 인증 코드는 {code} 이에요.\n10분 안에 입력해 주세요."
    html = (
        '<div style="font-family:sans-serif;line-height:1.6">'
        "<p>해독(偕讀) 가입 인증 코드예요.</p>"
        f'<p style="font-size:28px;font-weight:700;letter-spacing:6px">{code}</p>'
        "<p>10분 안에 입력해 주세요. 직접 요청하지 않았다면 이 메일을 무시해도 돼요.</p></div>"
    )
    attempts = await deliver(client, email, subject, text, html, secret=code)
    await record(session, email, "signup_code", attempts)
    return attempts[-1].ok


def transports(settings: Settings) -> list[str]:
    """설정에 따라 시도할 순서. 배포(Vercel)에서는 HTTP(Resend)를 먼저."""
    smtp = ["smtp465", "smtp587"] if settings.smtp_user and settings.smtp_password else []
    if settings.smtp_port not in (465, 587) and smtp:
        smtp = [f"smtp{settings.smtp_port}", *smtp]
    http = (["brevo"] if settings.brevo_api_key else []) + (["resend"] if settings.resend_api_key else [])
    order = http + smtp if settings.vercel_env else smtp + http
    return order or ["log"]


async def deliver(
    client: httpx.AsyncClient, to: str, subject: str, text: str, html: str, *, secret: str = ""
) -> list[Attempt]:
    """되는 전송 수단이 나올 때까지 차례로 시도한다. 마지막 원소의 ok가 최종 결과."""
    settings = get_settings()
    attempts: list[Attempt] = []
    for name in transports(settings):
        started = time.monotonic()
        try:
            if name == "log":
                if settings.vercel_env:
                    raise RuntimeError(
                        "메일 설정 없음 (BREVO_API_KEY, RESEND_API_KEY 또는 SMTP_USER/SMTP_PASSWORD)"
                    )
                log.warning("메일 설정 없음 — %s 에게 보낼 내용: %s", to, secret or subject)
            elif name == "brevo":
                await _send_brevo(client, settings, to, subject, text, html)
            elif name == "resend":
                await _send_resend(client, settings, to, subject, text, html)
            else:
                port = int(name.removeprefix("smtp"))
                await asyncio.to_thread(_send_smtp, settings, port, to, subject, text, html)
            attempts.append(Attempt(name, True, None, _ms(started)))
            return attempts
        except smtplib.SMTPAuthenticationError as exc:
            # 비밀번호 문제는 포트를 바꿔도 같다 — SMTP 는 더 안 두드리고 다음 수단으로
            attempts.append(Attempt(name, False, _err(exc), _ms(started)))
            log.error("SMTP 인증 실패: %s", _err(exc))
        except (smtplib.SMTPException, OSError, httpx.HTTPError, RuntimeError) as exc:
            attempts.append(Attempt(name, False, _err(exc), _ms(started)))
            log.error("메일 %s 실패: %s", name, _err(exc))
    return attempts


async def record(session: AsyncSession | None, to: str, kind: str, attempts: list[Attempt]) -> None:
    if session is None:
        return
    for a in attempts:
        row = MailLog(to_email=to, kind=kind, ok=a.ok, transport=a.transport)
        row.error, row.duration_ms = a.error, a.duration_ms
        session.add(row)
    try:
        await session.commit()
    except Exception as exc:  # 기록 실패가 발송 결과를 바꾸면 안 된다
        log.error("mail_logs 기록 실패: %s", exc)
        await session.rollback()


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _err(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"[:500]


async def _send_resend(client, settings: Settings, to: str, subject: str, text: str, html: str) -> None:
    payload = {"from": settings.resend_from, "to": [to], "subject": subject, "text": text, "html": html}
    r = await client.post(
        RESEND_URL,
        json=payload,
        headers={"Authorization": f"Bearer {settings.resend_api_key}"},
        timeout=RESEND_TIMEOUT_S,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Resend {r.status_code}: {r.text[:300]}")


def _split_from(value: str) -> tuple[str, str]:
    """'해독 <a@b.com>' → ('해독', 'a@b.com'). 주소만 있으면 이름은 '해독'."""
    name, _, addr = value.rpartition("<")
    if not addr:
        return "해독", value.strip()
    return name.strip() or "해독", addr.rstrip(">").strip()


async def _send_brevo(client, settings: Settings, to: str, subject: str, text: str, html: str) -> None:
    name, addr = _split_from(settings.brevo_from)
    payload = {
        "sender": {"name": name, "email": addr},
        "to": [{"email": to}],
        "subject": subject,
        "textContent": text,
        "htmlContent": html,
    }
    r = await client.post(
        BREVO_URL,
        json=payload,
        headers={"api-key": settings.brevo_api_key, "accept": "application/json"},
        timeout=RESEND_TIMEOUT_S,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Brevo {r.status_code}: {r.text[:300]}")


def _send_smtp(settings: Settings, port: int, to: str, subject: str, text: str, html: str) -> None:
    """465는 SSL, 그 밖(587)은 STARTTLS. 스레드에서 돌리며 연결·응답마다 타임아웃을 건다."""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"해독 <{settings.smtp_user}>"
    msg["To"] = to
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    user, pw = settings.smtp_user.strip(), settings.smtp_password.replace(" ", "").strip()
    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(settings.smtp_host, port, context=ctx, timeout=SMTP_TIMEOUT_S) as s:
            s.login(user, pw)
            s.send_message(msg)
    else:
        with smtplib.SMTP(settings.smtp_host, port, timeout=SMTP_TIMEOUT_S) as s:
            s.ehlo()
            s.starttls(context=ctx)
            s.ehlo()
            s.login(user, pw)
            s.send_message(msg)
