"""검색엔진·공유 메타: robots/sitemap, OG 태그, 앱 화면 noindex, 공개 주소 설정."""

from app.core.config import Settings
from tests.conftest import signup
from tests.test_api import upstream


async def test_robots_allows_public_and_blocks_app(api):
    async with api(upstream) as c:
        r = await c.get("/robots.txt")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    body = r.text
    assert "User-agent: *" in body and "Yeti" not in body  # 네이버도 같은 규칙
    for allowed in ("Allow: /\n", "Allow: /intro", "Allow: /join/"):
        assert allowed in body
    for blocked in ("Disallow: /admin", "Disallow: /api/", "Disallow: /books/", "Disallow: /me"):
        assert blocked in body
    assert "Sitemap: http://test/sitemap.xml" in body


async def test_sitemap_lists_only_public_pages(api):
    async with api(upstream) as c:
        r = await c.get("/sitemap.xml")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/xml")
    assert "<loc>http://test/</loc>" in r.text and "<loc>http://test/intro</loc>" in r.text
    assert "/races" not in r.text and "/library" not in r.text


async def test_default_og_image_is_served(api):
    async with api(upstream) as c:
        r = await c.get("/og/default.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n" and len(r.content) <= 300_000


async def test_landing_has_meta_og_canonical_and_icons(api):
    async with api(upstream) as c:
        html = (await c.get("/")).text
    assert '<meta name="description" content="' in html
    assert 'property="og:title"' in html and 'property="og:locale" content="ko_KR"' in html
    assert 'property="og:image" content="http://test/og/default.png"' in html
    assert 'property="og:url" content="http://test/"' in html and 'property="og:type" content="website"' in html
    assert '<link rel="canonical" href="http://test/">' in html
    assert 'name="twitter:card" content="summary_large_image"' in html
    assert 'href="/static/favicon.svg"' in html and 'href="/static/favicon.ico"' in html
    assert 'rel="apple-touch-icon" sizes="180x180"' in html
    assert 'name="robots"' not in html  # 랜딩은 색인된다


async def test_authenticated_pages_are_noindex(api):
    async with api(upstream) as c:
        await signup(c)
        home = (await c.get("/")).text
        library = (await c.get("/library")).text
    assert '<meta name="robots" content="noindex, nofollow">' in home
    assert '<meta name="robots" content="noindex, nofollow">' in library


async def test_static_favicons_exist(api):
    async with api(upstream) as c:
        for path in ("/static/favicon.svg", "/static/favicon.ico", "/static/favicon-180.png"):
            assert (await c.get(path)).status_code == 200, path


def test_site_url_setting_prefers_explicit_then_vercel():
    assert Settings(site_url="https://haedok.app/").public_base("http://x/") == "https://haedok.app"
    assert Settings(vercel_project_production_url="haedok.vercel.app").public_base() == "https://haedok.vercel.app"
    assert Settings(vercel_url="haedok-abc.vercel.app").public_base("http://local/") == "https://haedok-abc.vercel.app"
    assert Settings().public_base("http://localhost:8000/") == "http://localhost:8000"
