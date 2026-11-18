"""hardening: CSP는 nonce로 강제하고, 모든 화면의 인라인 <script>는 그 요청의 nonce를 단다.

화면마다 HTML을 실제로 파싱해서 확인한다 — 인라인 스크립트·인라인 이벤트 속성(onclick= 등)·
고유한 <title>·<h1> 한 개.
"""

import re
from html.parser import HTMLParser

import httpx
import pytest
from starlette.requests import Request

from app.core.config import get_settings
from app.web import STORE
from tests.conftest import signup
from tests.test_api import upstream
from tests.test_social import _race_pair, token_of


class PageScan(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.inline_scripts: list[str | None] = []  # 인라인 스크립트의 nonce 값(없으면 None)
        self.handlers: list[str] = []
        self.titles: list[str] = []
        self.h1 = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "script" and "src" not in a and a.get("type", "text/javascript") != "application/ld+json":
            self.inline_scripts.append(a.get("nonce"))
        self.handlers += [f"<{tag} {k}>" for k, _ in attrs if k.startswith("on")]
        if tag == "title":
            self._in_title = True
            self.titles.append("")
        if tag == "h1":
            self.h1 += 1

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.titles[-1] += data


def scan(html: str) -> PageScan:
    p = PageScan()
    p.feed(html)
    return p


def nonce_of(r: httpx.Response) -> str:
    csp = r.headers["content-security-policy"]
    m = re.search(r"script-src [^;]*'nonce-([A-Za-z0-9+/_=-]+)'", csp)
    assert m, csp
    return m.group(1)


# 인증 화면은 다른 작업(auth)이 고치는 중이라 <h1> 검사만 빼 둔다 — nonce·title 검사는 똑같이 한다
AUTH_OWNED = {"anon /login", "anon /signup", "/signup/verify"}


def check_page(r: httpx.Response, label: str) -> PageScan:
    assert r.status_code in (200, 404), (label, r.status_code)
    assert "text/html" in r.headers["content-type"], label
    assert "content-security-policy-report-only" not in r.headers, label
    csp = r.headers["content-security-policy"]
    script_src = re.search(r"script-src ([^;]*)", csp).group(1)
    assert "'unsafe-inline'" not in script_src, label
    assert "'unsafe-inline'" in re.search(r"style-src ([^;]*)", csp).group(1)  # style="" 속성은 그대로
    nonce = nonce_of(r)
    p = scan(r.text)
    assert all(n == nonce for n in p.inline_scripts), (label, p.inline_scripts, nonce)
    assert not p.handlers, (label, p.handlers)
    assert len(p.titles) == 1 and p.titles[0].strip(), (label, p.titles)
    if label not in AUTH_OWNED:
        assert p.h1 == 1, (label, p.h1)
    return p


@pytest.fixture
def admin_emails(monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_emails", "host@example.com")


async def test_csp_is_enforced_with_a_fresh_nonce_per_request(api):
    async with api(upstream) as c:
        a, b = await c.get("/login"), await c.get("/login")
        h = await c.get("/health")
    assert nonce_of(a) != nonce_of(b)
    assert len(nonce_of(a)) >= 16
    assert "content-security-policy" in h.headers  # JSON 응답에도 같은 정책(스크립트가 없어 무해)
    csp = a.headers["content-security-policy"]
    for directive in ("default-src 'self'", "frame-ancestors 'none'", "base-uri 'self'", "object-src 'none'"):
        assert directive in csp


async def test_report_only_is_opt_in(monkeypatch):
    from app.core.config import Settings

    assert Settings.model_fields["csp_report_only"].default is False


async def test_every_page_has_nonced_scripts_unique_title_and_one_h1(api, admin_emails):
    titles: dict[str, str] = {}

    def record(label: str, r: httpx.Response) -> None:
        p = check_page(r, label)
        titles[label] = p.titles[0].strip()

    async with api(upstream) as host, api(upstream) as friend, api(upstream) as anon:
        # 로그인 전
        for path in ("/", "/login", "/signup", "/guide", "/docs", "/developers", "/no-such-page"):
            record(f"anon {path}", await anon.get(path, headers={"accept": "text/html"}))

        book_id = await _race_pair(host, friend)  # host 는 관리자
        races = await host.get("/races")
        room = re.search(r'href="/races/(\d+)"', races.text).group(1)
        race = await host.get(f"/races/{room}")
        code = re.search(r'id="code">([A-Z0-9]{6})<', race.text).group(1)
        record("anon /join/{code}", await anon.get(f"/join/{code}"))
        await signup(anon, email="third@example.com", nickname="셋째")
        record("/join/{code}", await anon.get(f"/join/{code}"))

        for path in (
            "/",
            "/discover",
            "/discover?g=all",
            "/search?q=긴글",
            "/library",
            "/search",
            "/upload",
            "/me",
            "/leaderboard",
            "/races",
            f"/races/{room}",
            f"/books/{book_id}",
            f"/store/{STORE[0]['id']}",
            "/admin",
            "/admin/users",
            "/admin/rooms",
            "/admin/mail",
        ):
            record(path, await host.get(path))

    # 같은 제목을 쓰는 화면이 없어야 한다
    dupes = {t: [k for k, v in titles.items() if v == t] for t in titles.values()}
    assert {t: ks for t, ks in dupes.items() if len(ks) > 1} == {}


async def test_signup_verify_page_has_nonced_scripts(api):
    async with api(upstream) as c:
        page = await c.get("/signup")
        token = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
        await c.post(
            "/signup",
            data={
                "nickname": "독자",
                "email": "v@example.com",
                "password": "password123",
                "password_confirm": "password123",
                "csrf": token,
            },
        )
        check_page(await c.get("/signup/verify"), "/signup/verify")


async def test_500_page_has_nonce_and_matching_header():
    from app import main

    scope = {"type": "http", "method": "GET", "path": "/boom", "headers": [], "query_string": b""}
    r = main._error_page(Request(scope), 500)
    html = r.body.decode()
    csp = r.headers["content-security-policy"]
    nonce = re.search(r"'nonce-([^']+)'", csp).group(1)
    p = scan(html)
    assert p.inline_scripts and all(n == nonce for n in p.inline_scripts)


async def test_admin_delete_confirm_moved_out_of_inline_handler(api, admin_emails):
    async with api(upstream) as host, api(upstream) as friend:
        await _race_pair(host, friend)
        html = (await host.get("/admin/users")).text
    assert "onsubmit" not in html and 'data-confirm="' in html
    assert token_of(html)


async def test_offline_page_has_no_inline_script(api):
    async with api(upstream) as c:
        html = (await c.get("/static/offline.html")).text
        js = await c.get("/static/offline.js")
    p = scan(html)
    assert p.inline_scripts == [] and p.handlers == []
    assert '<script src="/static/offline.js"' in html and js.status_code == 200
