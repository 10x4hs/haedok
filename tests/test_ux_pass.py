"""품질·접근성·PWA 보강: 매니페스트 확장, 서비스 워커 빌드 도장, 공통 빈 상태·토스트 자리."""

from pathlib import Path

from app.pwa import BUILD_STAMP
from tests.test_api import upstream

STATIC = Path(__file__).parents[1] / "app" / "static"


async def test_manifest_has_shortcuts_screenshots_and_maskable(api):
    async with api(upstream) as c:
        m = (await c.get("/manifest.webmanifest")).json()
    assert m["display_override"] == ["standalone", "minimal-ui"]
    assert [s["name"] for s in m["shortcuts"]] == ["내 서재", "대결방", "찾기"]
    assert all(s["url"].startswith("/") for s in m["shortcuts"])
    assert len(m["screenshots"]) >= 2 and all(s["form_factor"] == "narrow" for s in m["screenshots"])
    for shot in m["screenshots"]:
        assert (STATIC / shot["src"].removeprefix("/static/")).is_file(), shot["src"]
    maskable = [i for i in m["icons"] if i["purpose"] == "maskable"]
    assert len(maskable) == 1 and maskable[0]["sizes"] == "512x512"


async def test_service_worker_carries_build_stamp(api):
    async with api(upstream) as c:
        sw = (await c.get("/sw.js")).text
    assert BUILD_STAMP and len(BUILD_STAMP) == 10
    assert f"const VERSION = 'haedok-{BUILD_STAMP}'" in sw and "__BUILD__" not in sw
    assert "RUNTIME_MAX = 60" in sw and ".mp4" in sw


async def test_offline_page_is_styled_and_retries(api):
    async with api(upstream) as c:
        html = (await c.get("/static/offline.html")).text
    assert "인터넷에 연결되어 있지 않아요" in html and "다시 시도" in html and "prefers-color-scheme" in html


async def test_base_preconnects_font_cdns(api):
    async with api(upstream) as c:
        html = (await c.get("/login")).text
    assert (
        'rel="preconnect" href="https://fonts.gstatic.com"' in html
        and 'rel="preconnect" href="https://cdn.jsdelivr.net"' in html
    )
