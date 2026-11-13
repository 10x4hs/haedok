import logging
import os
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from app import admin, auth, intro, pwa, seo, web
from app.api import books, me, points, social
from app.core.config import Settings, get_settings
from app.core.logging import request_id_var, setup_logging
from app.db.session import get_session

USER_AGENT = "haedok/0.1 (+https://github.com/10x4hs/haedok)"  # Wikimedia는 UA 명시 요구
VERSION = "0.1.0"

_settings = get_settings()
setup_logging(_settings.log_level, _settings.log_json)
log = logging.getLogger("haedok")
access_log = logging.getLogger("haedok.access")

# --- 오류 수집(Sentry): DSN이 있을 때만. 이메일·인증 코드 같은 개인정보는 보내기 전에 지운다 ---
_SCRUB_KEYS = {"email", "code", "password", "password_confirm", "csrf", "to_email", "signup_email"}


def _scrub(obj):
    if isinstance(obj, dict):
        return {k: ("[filtered]" if str(k).lower() in _SCRUB_KEYS else _scrub(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_scrub(v) for v in obj]
    return obj


def scrub_event(event: dict, hint) -> dict:
    for section in ("request", "extra", "contexts", "user"):
        if section in event:
            event[section] = _scrub(event[section])
    return event


if _settings.sentry_dsn:
    import sentry_sdk

    sentry_sdk.init(
        dsn=_settings.sentry_dsn,
        traces_sample_rate=0.1,
        send_default_pii=False,
        before_send=scrub_event,
        release=f"haedok@{VERSION}",
        environment=_settings.vercel_env or "local",
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    if settings.database_url.startswith("sqlite"):
        # 임시 배포(서버리스 /tmp SQLite)·로컬 시연용: 테이블이 없으면 만든다. Postgres는 Alembic이 맡는다
        from app import models  # noqa: F401
        from app.db.base import Base
        from app.db.session import engine

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    async with httpx.AsyncClient(
        timeout=settings.http_timeout_seconds,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        app.state.http = client
        yield


app = FastAPI(title="해독(偕讀) API", version=VERSION, lifespan=lifespan)
if os.environ.get("VERCEL") and _settings.secret_key == Settings.model_fields["secret_key"].default:
    # 기본 키는 공개 저장소에 있어 누구나 세션 쿠키를 위조할 수 있다
    raise RuntimeError("SECRET_KEY 환경 변수를 설정해야 배포할 수 있어요")
app.add_middleware(
    SessionMiddleware,
    secret_key=_settings.secret_key,
    session_cookie="session",
    max_age=_settings.session_idle_minutes * 60,
    same_site="lax",
    https_only=_settings.cookie_secure,
)


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",  # 다른 사이트가 이 앱을 iframe에 넣어 클릭을 가로채지 못하게
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}

# Content-Security-Policy. 템플릿에 인라인 <script>(14곳)·style="" 속성(다수)·onsubmit(관리자)이 남아 있어
# 지금은 Report-Only로 내보내 위반만 본다. 인라인을 모두 nonce로 바꾸면 CSP_REPORT_ONLY=false로 강제한다.
CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://unpkg.com",
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com",
        "font-src 'self' data: https://cdn.jsdelivr.net https://fonts.gstatic.com",
        "img-src 'self' data: blob: https:",
        "media-src 'self' data: blob:",
        "connect-src 'self' ws: wss:",
        "worker-src 'self'",
        "frame-ancestors 'none'",
        "base-uri 'self'",
        "form-action 'self'",
    ]
)
CSP_HEADER = "Content-Security-Policy-Report-Only" if _settings.csp_report_only else "Content-Security-Policy"


@app.middleware("http")
async def observe_request(request: Request, call_next):
    """요청 id(x-vercel-id 또는 uuid4)를 로그에 묶고, 응답에 x-request-id와 보안 헤더를 단다."""
    rid = request.headers.get("x-vercel-id") or uuid.uuid4().hex
    token = request_id_var.set(rid)
    started = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
    finally:
        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        request_id_var.reset(token)
        if request.url.path != "/health" and not request.url.path.startswith("/static/"):
            access_log.log(
                logging.WARNING if status >= 500 else logging.INFO,
                "request",
                extra={
                    "request_id": rid,
                    "method": request.method,
                    "path": request.url.path,
                    "status": status,
                    "duration_ms": duration_ms,
                },
            )
    response.headers["x-request-id"] = rid
    for key, value in SECURITY_HEADERS.items():
        response.headers.setdefault(key, value)
    response.headers.setdefault(CSP_HEADER, CSP)
    if _settings.cookie_secure:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains; preload"
        )
    return response


@app.exception_handler(auth.LoginRequired)
async def to_login(request: Request, exc: auth.LoginRequired) -> RedirectResponse:
    return RedirectResponse(f"/login?next={request.url.path}", status_code=303)


# --- 오류 화면: 화면 요청에는 앱 디자인의 HTML, API·JSON 요청에는 JSON ---
error_templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def _wants_html(request: Request) -> bool:
    if request.url.path.startswith("/api/"):
        return False
    return "text/html" in request.headers.get("accept", "") or "*/*" in request.headers.get("accept", "")


def _error_page(request: Request, status: int) -> HTMLResponse:
    from app.core import appearance

    ctx = {"look": appearance.resolve(None), "user": None, "csrf": "", "path": request.url.path}
    ctx["request_id"] = request_id_var.get() or request.headers.get("x-vercel-id") or ""
    return error_templates.TemplateResponse(request, f"errors/{status}.html", ctx, status_code=status)


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404 and _wants_html(request):
        return _error_page(request, 404)
    headers = getattr(exc, "headers", None)
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=headers)


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    """예외 내용은 로그(와 Sentry)에만 남기고 화면에는 절대 내보내지 않는다."""
    log.exception("unhandled error on %s %s", request.method, request.url.path)
    if _wants_html(request):
        return _error_page(request, 500)
    return JSONResponse({"detail": "서버에 문제가 생겼어요. 잠시 뒤 다시 시도해 주세요"}, status_code=500)


app.include_router(books.router)
app.include_router(me.router)
app.include_router(points.router)
app.include_router(social.router)
app.include_router(web.router)
app.include_router(admin.router)
app.include_router(intro.router)
app.include_router(pwa.router)
app.include_router(seo.router)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


@app.get("/health")
async def health(session: AsyncSession = Depends(get_session)) -> JSONResponse:
    """DB에 SELECT 1이 닿으면 200, 아니면 503 — Vercel·외부 모니터가 본다."""
    body = {"status": "ok", "db": "ok", "version": VERSION}
    try:
        await session.execute(text("SELECT 1"))
    except Exception as e:  # noqa: BLE001 — 어떤 DB 오류든 503으로
        log.warning("health: db check failed: %s", type(e).__name__)
        body |= {"status": "degraded", "db": "error"}
        return JSONResponse(body, status_code=503)
    return JSONResponse(body)
