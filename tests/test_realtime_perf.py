"""실시간 스위치(REALTIME_WS)와 소켓의 DB 연결 반납."""

import re

import pytest
from starlette.websockets import WebSocketDisconnect

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


