from functools import lru_cache
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "haedok"
    database_url: str = "postgresql+asyncpg://haedok:haedok@localhost:5432/haedok"

    @field_validator("database_url")
    @classmethod
    def _asyncpg_url(cls, v: str) -> str:
        """호스팅 Postgres(Neon 등)가 주는 postgres://…?sslmode=require 를 asyncpg 형식으로 바꾼다."""
        if v.startswith("postgres://"):
            v = "postgresql://" + v.removeprefix("postgres://")
        if v.startswith("postgresql://"):
            v = "postgresql+asyncpg://" + v.removeprefix("postgresql://")
        if v.startswith("postgresql+asyncpg://"):
            # asyncpg가 모르는 쿼리 인자(sslmode, channel_binding)를 정리한다 — 순서와 상관없이
            parts = urlsplit(v)
            query = []
            for key, val in parse_qsl(parts.query):
                if key == "channel_binding":
                    continue
                query.append(("ssl", val) if key == "sslmode" else (key, val))
            v = urlunsplit(parts._replace(query=urlencode(query)))
        return v

    # 외부 소스별 타임아웃. 한 소스가 느려도 통합 검색 전체가 막히지 않도록 짧게 둔다.
    http_timeout_seconds: float = 3.0

    # 키가 비어 있는 소스는 자동 비활성화된다 (registry.build_sources 참고).
    kakao_rest_api_key: str | None = None
    naver_client_id: str | None = None
    naver_client_secret: str | None = None
    google_books_api_key: str | None = None
    wikisource_lang: str = "ko"

    # 세션 쿠키 서명 키. 운영에서는 반드시 .env로 바꾼다
    secret_key: str = "dev-only-change-me"
    session_idle_minutes: int = 30
    cookie_secure: bool = False
    # 시연·로컬 테스트용 최소 체류시간 배율. 운영은 반드시 1.0
    dwell_scale: float = 1.0
    # 업로드 상한(MB). Vercel은 요청 본문을 4.5MB에서 끊으므로 배포에서는 4로 둔다
    max_upload_mb: int = 20
    # 가입 인증 메일(Resend). 키가 없으면 코드를 로그에만 남긴다
    # Brevo(구 Sendinblue) HTTP API — 도메인 없이 "발신 이메일 주소 인증"만으로 보낼 수 있다(무료 300통/일)
    brevo_api_key: str | None = None
    brevo_from: str = "해독 <1gmltjd0@gmail.com>"
    resend_api_key: str | None = None
    resend_from: str = "해독 <onboarding@resend.dev>"
    # Gmail 등 SMTP(우선 사용). Gmail은 계정 주소 + 16자리 앱 비밀번호
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 465
    smtp_user: str | None = None
    smtp_password: str | None = None
    # Vercel이 넣어 주는 값(production/preview). 있으면 메일 설정이 없을 때 로그 대신 실패로 처리한다
    vercel_env: str | None = None
    # 관리자 화면(/admin)을 열 수 있는 이메일 — 쉼표로 여러 개, 대소문자 무시. 비어 있으면 아무도 못 연다
    admin_emails: str = ""

    def admin_email_set(self) -> frozenset[str]:
        return frozenset(e.strip().lower() for e in self.admin_emails.split(",") if e.strip())

    # 안드로이드 TWA 앱 — /.well-known/assetlinks.json 에 실린다. 지문은 쉼표로 여러 개
    android_package: str = "app.vercel.saipr_reading.twa"
    android_cert_sha256: str = (
        "7F:D4:97:F9:FC:B0:1F:F9:A2:CB:2F:1A:A8:BE:65:21:F7:C9:08:3B:34:96:A4:E3:8F:F9:CB:41:7C:35:06:48"
    )

    # --- growth ---
    # 공개 주소(https://…, 끝 슬래시 없이). canonical·OG·사이트맵에 쓴다.
    # 비어 있으면 Vercel이 주는 VERCEL_PROJECT_PRODUCTION_URL → VERCEL_URL → 요청의 base URL 순으로 정한다
    site_url: str | None = None
    vercel_project_production_url: str | None = None
    vercel_url: str | None = None

    def public_base(self, request_base: str = "") -> str:
        host = self.site_url or self.vercel_project_production_url or self.vercel_url or request_base
        if host and "://" not in host:
            host = "https://" + host
        return host.rstrip("/")

    # --- ops ---
    # Sentry DSN. 비워 두면 오류 수집을 하지 않는다 (로컬·테스트 기본)
    sentry_dsn: str | None = None
    # 로그 레벨(stdlib). 운영은 INFO, 디버깅은 DEBUG
    log_level: str = "INFO"
    # 로그를 JSON 한 줄로 낼지(서버·Vercel) 사람이 읽기 쉬운 텍스트로 낼지(로컬)
    log_json: bool = True
    # CSP를 Report-Only로 내보낼지. 템플릿 인라인 스크립트가 모두 nonce를 달면 false로 바꿔 강제한다
    csp_report_only: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
