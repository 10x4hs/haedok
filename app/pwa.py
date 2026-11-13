"""PWA·안드로이드 앱(TWA) 연결 파일.

- /manifest.webmanifest, /sw.js: 서비스 워커는 사이트 전체(/)를 맡아야 해서 루트 경로로 내보낸다
- /.well-known/assetlinks.json: 안드로이드 TWA 앱이 이 사이트의 것임을 증명 (주소창 없이 열림)
"""

import hashlib
import mimetypes
import os
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response

from app.core.config import get_settings

router = APIRouter(include_in_schema=False)
# 서버리스 이미지에 따라 .apk 형식을 모를 수 있어 직접 등록한다 (안드로이드가 설치 파일로 알아보게)
mimetypes.add_type("application/vnd.android.package-archive", ".apk")
STATIC = Path(__file__).parent / "static"


def build_stamp() -> str:
    """서비스 워커 캐시 이름에 들어가는 빌드 도장.

    Vercel은 배포마다 커밋 SHA를 주므로 그걸 쓰고, 로컬·도커는 정적 파일 내용의 해시를 쓴다.
    정적 파일이 하나라도 바뀌면 도장이 바뀌어 옛 캐시가 정리된다 (sw.js의 VERSION을 손으로 올릴 필요가 없다).
    """
    sha = os.environ.get("VERCEL_GIT_COMMIT_SHA", "")
    if sha:
        return sha[:10]
    h = hashlib.sha1()
    for f in sorted(STATIC.glob("*.css")) + sorted(STATIC.glob("*.js")) + [STATIC / "offline.html"]:
        if f.is_file():
            h.update(f.name.encode())
            h.update(f.read_bytes())
    return h.hexdigest()[:10]


BUILD_STAMP = build_stamp()


def _shortcut(name: str, short: str, url: str) -> dict:
    """홈 화면 아이콘을 길게 눌렀을 때 나오는 바로가기."""
    return {
        "name": name,
        "short_name": short,
        "url": f"{url}?source=pwa",
        "icons": [{"src": "/static/icons/icon-192.png", "sizes": "192x192"}],
    }


MANIFEST = {
    "id": "/",
    "name": "偕讀 해독 — 같은 책을 함께 읽다",
    "short_name": "해독",
    "description": "같은 책을 각자의 속도로 함께 읽는 소셜 독서 앱",
    "lang": "ko",
    "start_url": "/?source=pwa",
    "scope": "/",
    "display": "standalone",
    "orientation": "portrait",
    "background_color": "#0a0a0a",
    "theme_color": "#0a0a0a",
    "display_override": ["standalone", "minimal-ui"],
    "categories": ["books", "education", "social"],
    "shortcuts": [
        _shortcut("내 서재", "서재", "/library"),
        _shortcut("대결방", "대결", "/races"),
        _shortcut("찾기", "찾기", "/search"),
    ],
    "screenshots": [
        {
            "src": "/static/icons/screenshots/reader.png",
            "sizes": "780x1688",
            "type": "image/png",
            "form_factor": "narrow",
            "label": "3D로 책장을 넘기며 읽는 화면",
        },
        {
            "src": "/static/icons/screenshots/races.png",
            "sizes": "780x1688",
            "type": "image/png",
            "form_factor": "narrow",
            "label": "친구와 같은 책으로 페이지 레이스",
        },
    ],
    "icons": [
        {"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
        {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
        {
            "src": "/static/icons/icon-maskable-512.png",
            "sizes": "512x512",
            "type": "image/png",
            "purpose": "maskable",
        },
    ],
}


@router.get("/manifest.webmanifest")
async def manifest() -> JSONResponse:
    return JSONResponse(
        MANIFEST, media_type="application/manifest+json", headers={"Cache-Control": "public, max-age=3600"}
    )


@router.get("/sw.js")
async def service_worker() -> Response:
    # __BUILD__ 자리에 빌드 도장을 넣어 보낸다 — 배포마다 캐시 이름이 바뀐다
    body = (STATIC / "sw.js").read_text(encoding="utf-8").replace("__BUILD__", BUILD_STAMP)
    return Response(
        body,
        media_type="text/javascript",
        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"},
    )


def asset_links() -> list[dict]:
    settings = get_settings()
    prints = [p.strip().upper() for p in settings.android_cert_sha256.split(",") if p.strip()]
    if not prints:
        return []
    return [
        {
            "relation": ["delegate_permission/common.handle_all_urls"],
            "target": {
                "namespace": "android_app",
                "package_name": settings.android_package,
                "sha256_cert_fingerprints": prints,
            },
        }
    ]


@router.get("/.well-known/assetlinks.json")
async def assetlinks() -> JSONResponse:
    return JSONResponse(asset_links(), headers={"Cache-Control": "public, max-age=3600"})
