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

    # 서점 검색(app/services/bookstore.py) — 고른 책(app/data/store.json) 밖의 책을 찾아 온다.
    # 카카오 책 검색 API 키(KAKAO_REST_API_KEY)가 있으면 그걸 쓴다(공식, 무료).
    # bookstore_live=true는 키 없이 알라딘 검색 화면을 읽는 방식이다.
    # 알라딘 robots.txt가 막는 경로라 기본은 꺼 둔다.
    # 둘 다 없으면 고른 책 안에서만 찾는다
    bookstore_live: bool = False

    # 세션 쿠키 서명 키. 운영에서는 반드시 .env로 바꾼다
    secret_key: str = "dev-only-change-me"
    session_idle_minutes: int = 30
    cookie_secure: bool = False
    # 시연·로컬 테스트용 최소 체류시간 배율. 운영은 반드시 1.0
    dwell_scale: float = 1.0
    # 업로드 상한(MB). Vercel은 요청 본문을 4.5MB에서 끊으므로 배포에서는 4로 둔다
    max_upload_mb: int = 20
    # 가입 인증 메일(Resend). 키가 없으면 코드를 로그에만 남긴다
    # Google Apps Script 웹 앱 — 이 Gmail로 누구에게나 보낸다(일반 계정 하루 100명). scripts/gas_mailer.gs
    gas_mail_url: str | None = None
    gas_mail_secret: str = ""
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
    # 마일리지 화면(/mileage)과 리워드 관리(/admin/rewards). 끄면 입구가 숨고 주소는 404 (FEATURE_MILEAGE)
    feature_mileage: bool = True

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
    # CSP를 Report-Only로 내보낼지. 기본은 강제(false) — 문제를 살펴볼 때만 잠깐 true로 둔다
    csp_report_only: bool = False

    # --- realtime ---
    # 친구 소식을 WebSocket으로 밀어 줄지(REALTIME_WS). 비워 두면 로컬·도커는 켜고 Vercel(VERCEL_ENV)은 끈다.
    # Vercel Python 함수도 이제 소켓을 열 수 있지만, app/realtime.py 브로커는 인스턴스 메모리 안에만 있어서
    # 다른 인스턴스에 붙은 친구의 소식은 오지 않는다. 브로커를 Redis 등으로 바꾸기 전까지는 폴링이 정답이다
    realtime_ws: bool | None = None
    # 같이 읽을 사람 모집 게시판(/together). 끄면 입구가 숨고 주소는 404 (FEATURE_BOARD)
    feature_board: bool = True

    # 하루 목표 · 잔디 · 연속 읽기(FEATURE_GOALS). 끄면 /api/v1/me/goal 은 404, /me·홈의 목표 칸도 숨는다
    feature_goals: bool = True

    def realtime_ws_enabled(self) -> bool:
        if self.realtime_ws is not None:
            return self.realtime_ws
        return not self.vercel_env

    # 친구 + 채팅(개인·방) 기능(FEATURE_CHAT). 끄면 메뉴·링크가 사라지고 /chat·/friends 경로가 404
    feature_chat: bool = True

    # 읽은 책 게시물(/community · /posts/new · /p/{id} · /u/{id}, FEATURE_POSTS). 끄면 입구가 숨고 주소는 404
    feature_posts: bool = True

    # --- AI (책에게 묻기 · 맞춤 추천 · AI 서점 도우미 · 독서 요약) ---
    # 특정 회사에 묶이지 않도록 OpenAI 호환 API(/chat/completions · /embeddings)를 쓴다.
    # OpenAI · Gemini(OpenAI 호환 주소) · DeepSeek · Groq · OpenRouter · Ollama 등
    # 어느 것이든 주소·키·모델만 바꾸면 된다.
    # AI_PROVIDER: "" (끔) | "openai_compat" | "fake"(키 없이 돌아가는 결정적 가짜 — 테스트·미리보기용)
    ai_provider: str = ""
    ai_base_url: str = "https://api.openai.com/v1"
    ai_api_key: str | None = None
    ai_chat_model: str = "gpt-4o-mini"
    # 임베딩만 다른 곳을 쓸 때(비우면 위 주소·키를 쓴다)
    ai_embed_base_url: str | None = None
    ai_embed_api_key: str | None = None
    ai_embed_model: str = "text-embedding-3-small"
    ai_embed_dim: int = 256  # 저장하는 벡터 길이 — 모델이 지원하면 dimensions 로 줄여 받는다
    ai_timeout_seconds: float = 20.0
    ai_daily_requests: int = 40  # 한 사람이 하루에 부를 수 있는 AI 답변 수(비용 상한)
    # 책에게 물어보기 · 나를 위한 다음 책 · /admin/ai (FEATURE_AI_RAG). AI 가 꺼져 있어도 입구가 숨는다
    feature_ai_rag: bool = True
    # AI 서점 도우미(/assistant) · 독서 요약(/me/recap) — AI 가 꺼져 있으면 이 값과 상관없이 숨는다
    feature_ai_shop: bool = True
    # 읽는 중의 AI — 문단에서 묻기 · AI 노트 · 지난 이야기 · 등장인물 · 생각해 볼 질문 (FEATURE_AI_READER)
    feature_ai_reader: bool = True

    @property
    def ai_enabled(self) -> bool:
        if self.ai_provider == "fake":
            return True
        return self.ai_provider == "openai_compat" and bool(self.ai_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
