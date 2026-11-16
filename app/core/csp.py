"""Content-Security-Policy — 요청마다 새 nonce를 만들어 인라인 <script>에만 실행을 허락한다.

미들웨어가 `request.state.csp_nonce`와 contextvar에 nonce를 넣고, Jinja 전역 `csp_nonce`가
그 값을 읽는다(템플릿: `<script nonce="{{ csp_nonce }}">`). style="" 속성은 많아서 style-src만
'unsafe-inline'을 남긴다 — 스타일 주입은 스크립트 실행보다 위험이 훨씬 작다.
"""

import secrets
from contextvars import ContextVar

csp_nonce_var: ContextVar[str | None] = ContextVar("csp_nonce", default=None)


def new_nonce() -> str:
    return secrets.token_urlsafe(18)  # 144비트, base64url — CSP nonce 문법에 맞는다


def current_nonce() -> str:
    """지금 요청의 nonce. 미들웨어 밖(서버 오류 화면 등)에서 불리면 새로 만들어 둔다."""
    value = csp_nonce_var.get()
    if not value:
        value = new_nonce()
        csp_nonce_var.set(value)
    return value


class NonceProxy:
    """Jinja 전역으로 넣는 지연 값 — 렌더링할 때마다 지금 요청의 nonce가 된다."""

    def __str__(self) -> str:
        return current_nonce()

    __html__ = __str__

    def __bool__(self) -> bool:
        return True


def policy(nonce: str) -> str:
    return "; ".join(
        [
            "default-src 'self'",
            f"script-src 'self' 'nonce-{nonce}' https://cdn.jsdelivr.net https://unpkg.com",
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com",
            "font-src 'self' data: https://cdn.jsdelivr.net https://fonts.gstatic.com",
            "img-src 'self' data: blob: https:",
            "media-src 'self' data: blob:",
            "connect-src 'self' ws: wss:",
            "worker-src 'self'",
            "object-src 'none'",
            "frame-ancestors 'none'",
            "base-uri 'self'",
            "form-action 'self'",
        ]
    )


def header_name(report_only: bool) -> str:
    return "Content-Security-Policy-Report-Only" if report_only else "Content-Security-Policy"
