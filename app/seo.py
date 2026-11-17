"""검색엔진·공유용 공개 파일 — robots.txt, sitemap.xml, 기본 OG 이미지.

- 공개 페이지(랜딩, /intro, 초대 미리보기 /join/*)만 허용하고 앱 화면은 막는다. 네이버 Yeti도 똑같이 들어온다
- OG 이미지는 scripts/build_og.py 로 미리 그려 둔 PNG 한 장(Pillow는 런타임 의존성이 아니다).
  카카오·슬랙 등 미리보기 봇은 OG 태그만 읽으므로 /join/{code} 는 로그인 없이 열려야 한다
"""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, PlainTextResponse, Response

from app.core.config import get_settings

router = APIRouter(include_in_schema=False)
STATIC = Path(__file__).parent / "static"
OG_DEFAULT = STATIC / "og" / "default.png"

PUBLIC_PATHS = ("/", "/intro", "/signup", "/login")
ALLOW = ("/", "/intro", "/join/", "/signup", "/login", "/static/", "/og/", "/manifest.webmanifest")
DISALLOW = (
    "/admin", "/api/", "/books/", "/races", "/library", "/me", "/discover", "/search",
    "/upload", "/store/", "/open/", "/shelf/", "/avatars/", "/signup/verify", "/logout", "/welcome/",
)  # fmt: skip
SITE_DESCRIPTION = "같은 책을 각자의 속도로 함께 읽는 소셜 독서 앱. 고전은 무료, 친구와 페이지 레이스."
CACHE = {"Cache-Control": "public, max-age=3600"}


def public_base(request: Request) -> str:
    """canonical·OG에 쓰는 절대 주소의 뿌리(끝 슬래시 없음)."""
    return get_settings().public_base(str(request.base_url))


def install(env) -> None:
    """Jinja 환경에 공개 주소 헬퍼를 넣는다 — base.html이 canonical·og:url·og:image를 만든다."""
    env.globals["public_base"] = public_base
    env.globals["SITE_DESCRIPTION"] = SITE_DESCRIPTION


@router.get("/robots.txt")
async def robots(request: Request) -> PlainTextResponse:
    base = public_base(request)
    lines = ["User-agent: *"]
    lines += [f"Allow: {p}" for p in ALLOW]
    lines += [f"Disallow: {p}" for p in DISALLOW]
    lines += ["", f"Sitemap: {base}/sitemap.xml", ""]
    return PlainTextResponse("\n".join(lines), headers=CACHE)


@router.get("/sitemap.xml")
async def sitemap(request: Request) -> Response:
    base = public_base(request)
    urls = "".join(f"<url><loc>{base}{p}</loc></url>" for p in PUBLIC_PATHS)
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'
    )
    return Response(xml, media_type="application/xml", headers=CACHE)


@router.get("/og/default.png")
async def og_default() -> FileResponse:
    return FileResponse(
        OG_DEFAULT, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"}
    )
