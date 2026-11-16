"""실시간 스위치(REALTIME_WS), 소켓의 DB 연결 반납, 정적 파일 캐시·버전, Speculation Rules."""

import json
import re

import pytest
from starlette.websockets import WebSocketDisconnect

from app.core import assets
from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.main import app
from tests.test_api import upstream
from tests.test_social import _race_pair, _signup_sync, live_client, token_of  # noqa: F401


@pytest.fixture
def ws_setting(monkeypatch):
    """lru_cache 된 설정 객체의 realtime_ws 를 테스트 동안만 바꾼다."""

    def set_(value: bool | None) -> None:
        monkeypatch.setattr(get_settings(), "realtime_ws", value)

    return set_


# --- REALTIME_WS ---


def test_realtime_ws_defaults_on_locally_and_off_on_vercel(monkeypatch):
    monkeypatch.delenv("REALTIME_WS", raising=False)
    monkeypatch.delenv("VERCEL_ENV", raising=False)
    assert Settings().realtime_ws_enabled() is True
    assert Settings(vercel_env="production").realtime_ws_enabled() is False
    assert Settings(vercel_env="preview").realtime_ws_enabled() is False


def test_realtime_ws_explicit_setting_wins(monkeypatch):
    monkeypatch.setenv("VERCEL_ENV", "production")
    monkeypatch.setenv("REALTIME_WS", "true")
    assert Settings().realtime_ws_enabled() is True
    monkeypatch.delenv("VERCEL_ENV")
    monkeypatch.setenv("REALTIME_WS", "false")
    assert Settings().realtime_ws_enabled() is False


@pytest.mark.parametrize("enabled", [True, False])
async def test_social_tells_the_reader_whether_to_open_a_socket(api, clock, ws_setting, enabled):
    ws_setting(enabled)
    async with api(upstream) as host, api(upstream) as friend:
        book_id = await _race_pair(host, friend)
        r = await friend.get(f"/api/v1/books/{book_id}/social")
    assert r.status_code == 200
    assert r.json()["live"] == {"ws": enabled}


def test_websocket_is_refused_when_realtime_ws_is_off(live_client, ws_setting):  # noqa: F811
    ws_setting(False)
    c = live_client
    _signup_sync(c, "off@example.com", "꺼짐")
    with pytest.raises(WebSocketDisconnect) as e, c.websocket_connect("/api/v1/ws/books/1") as ws:
        ws.receive_json()
    assert e.value.code == 4403


def test_websocket_releases_its_db_session_before_listening(live_client, ws_setting):  # noqa: F811
    """소켓이 열려 있는 동안 DB 연결(트랜잭션)을 붙들지 않는다 — 풀은 인스턴스당 5개뿐이다."""
    ws_setting(True)
    c = live_client
    _signup_sync(c, "a@example.com", "에이")
    r = c.post(
        "/races/start/wikisource/긴글",
        data={"csrf": token_of(c.get("/races").text), "days": "7"},
        follow_redirects=False,
    )
    book_id = re.search(r'href="/books/(\d+)"', c.get(r.headers["location"]).text).group(1)

    inner = app.dependency_overrides[get_session]
    opened = []

    async def tracking():
        async for s in inner():
            opened.append(s)
            yield s

    app.dependency_overrides[get_session] = tracking
    with c.websocket_connect(f"/api/v1/ws/books/{book_id}") as ws:
        ws.send_text("ping")
        assert len(opened) == 1
        session = opened[0]
        assert not session.in_transaction(), "the socket must not keep a DB transaction open"
        assert session.bind.sync_engine.pool.checkedout() == 0, "no pooled connection pinned by the socket"


# --- 정적 파일: ?v=<내용 해시> 와 Cache-Control ---


async def test_static_without_version_gets_a_short_cache(api):
    async with api(upstream) as c:
        r = await c.get("/static/app.css")
    assert r.status_code == 200
    assert r.headers["cache-control"] == assets.SHORT


async def test_static_with_current_version_is_immutable(api):
    v = assets.version_of("/static/app.css")
    assert re.fullmatch(r"[0-9a-f]{10}", v)
    async with api(upstream) as c:
        good = await c.get(f"/static/app.css?v={v}")
        stale = await c.get("/static/app.css?v=0000000000")
        empty = await c.get("/static/app.css?v=")
        missing = await c.get(f"/static/nope.css?v={v}")
    assert good.headers["cache-control"] == assets.IMMUTABLE
    assert stale.headers["cache-control"] == assets.SHORT, "a wrong version must not be pinned for a year"
    assert empty.headers["cache-control"] == assets.SHORT
    assert missing.status_code == 404 and "cache-control" not in missing.headers


async def test_static_large_files_still_served(api):
    """랜딩 영상과 안드로이드 APK는 그대로 /static 에서 내려간다(번들에서 빼면 안 된다)."""
    async with api(upstream) as c:
        apk = await c.head("/static/haedok.apk")
        mp4 = await c.head("/static/demo/haedok-demo.mp4")
    assert apk.status_code == 200 and mp4.status_code == 200


async def test_pages_link_css_and_js_with_their_version(api):
    async with api(upstream) as c:
        html = (await c.get("/")).text
    for path in ("/static/app.css", "/static/looks.css", "/static/fx.js", "/static/motion.js"):
        assert f'"{path}?v={assets.version_of(path)}"' in html, path
    assert not re.search(r'(?:href|src)="/static/[^"?]+\.(?:css|js)"', html), "every css/js link is versioned"


def test_add_versions_only_touches_plain_static_css_js_attributes():
    src = (
        '<link href="/static/app.css"><script src=\'/static/a/b.js\'></script>'
        '<img src="/static/intro/{{ n }}.jpg"><a href="/static/haedok.apk">'
        '<link href="/static/x.css?v=1"><link href="https://cdn.example/static/app.css">'
    )
    out = assets.add_versions(src)
    assert "href=\"/static/app.css?v={{ static_v('/static/app.css') }}\"" in out
    assert "src='/static/a/b.js?v={{ static_v('/static/a/b.js') }}'" in out
    for untouched in ('/static/intro/{{ n }}.jpg"', '/static/haedok.apk"', "/static/x.css?v=1\"", "cdn.example/static/app.css\""):
        assert untouched in out


def test_version_of_refuses_paths_outside_static():
    assert assets.version_of("/static/../core/config.py") == ""
    assert assets.version_of("/etc/passwd") == ""
    assert assets.version_of("/static/does-not-exist.css") == ""


def test_version_changes_with_content(tmp_path, monkeypatch):
    monkeypatch.setattr(assets, "STATIC_DIR", tmp_path)
    f = tmp_path / "a.css"
    f.write_text("a{}")
    first = assets.version_of("/static/a.css")
    f.write_text("a{color:red}")
    assert assets.version_of("/static/a.css") != first


# --- Speculation Rules ---


async def test_speculation_rules_carry_the_csp_nonce_and_skip_side_effect_routes(api):
    async with api(upstream) as c:
        r = await c.get("/")
    nonce = re.search(r"'nonce-([^']+)'", r.headers["content-security-policy"]).group(1)
    m = re.search(r'<script type="speculationrules" nonce="([^"]+)">(.*?)</script>', r.text, re.S)
    assert m, "speculation rules block is rendered"
    assert m.group(1) == nonce
    rules = json.loads(m.group(2))
    rule = rules["prerender"][0]
    assert rule["eagerness"] == "moderate"
    excluded = rule["where"]["and"][1]["not"]["href_matches"]
    for path in ("/logout", "/me/logout-all", "/api/*", "/books/*", "/open/*"):
        assert path in excluded
    assert "/admin" not in r.text, "non-admins never see the admin path"
    assert "[data-no-prerender]" in rule["where"]["and"][2]["not"]["selector_matches"]
