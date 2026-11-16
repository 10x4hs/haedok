"""hardening: 서버리스(Vercel)에서는 WebSocket이 없어 폴링으로 친구 소식을 받는다.

폴링이 읽는 엔드포인트(/social, /reactions, /presence)도 소켓과 똑같이 스포일러 가드를 지켜야 한다 —
내 검증 위치보다 뒤의 반응 본문은 어떤 폴링 응답에도 실리지 않는다.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from app.realtime import Broker
from tests.conftest import read_chunk, signup
from tests.test_api import upstream
from tests.test_social import _race_pair, token_of

ROOT = Path(__file__).resolve().parents[1]
SECRET = "범인은 결국 장인이었다"


async def _poll_all(client, book_id, token, chunk) -> list[str]:
    """reader.js 폴링(pollFriends)과 presence.js 박동이 부르는 응답을 모두 모은다."""
    social = await client.get(f"/api/v1/books/{book_id}/social")
    reactions = await client.get(f"/api/v1/books/{book_id}/reactions", params={"chunk": chunk})
    presence = await client.put(
        f"/api/v1/books/{book_id}/presence", json={"chunk_index": chunk}, headers={"X-CSRF-Token": token}
    )
    for r in (social, reactions, presence):
        assert r.status_code == 200, r.text
    return [social.text, reactions.text, presence.text]


async def test_polling_never_leaks_reaction_bodies_ahead_of_me(api, clock):
    async with api(upstream) as host, api(upstream) as friend:
        book_id = await _race_pair(host, friend)
        ht = token_of((await host.get(f"/books/{book_id}")).text)
        ft = token_of((await friend.get(f"/books/{book_id}")).text)
        for i in range(3):
            await read_chunk(host, book_id, i, ht, clock)
        r = await host.post(
            f"/api/v1/books/{book_id}/reactions",
            json={"chunk_index": 2, "anchor": 0, "comment": SECRET},
            headers={"X-CSRF-Token": ht},
        )
        assert r.status_code == 201

        # 친구는 0쪽만 읽었다. 어느 쪽을 물어도(앞 쪽, 지금 읽는 쪽, 범위 밖) 본문은 없다
        await read_chunk(friend, book_id, 0, ft, clock)
        for chunk in (0, 1, 2, 99):
            for body in await _poll_all(friend, book_id, ft, chunk):
                assert SECRET not in body, (chunk, body)
        social = (await friend.get(f"/api/v1/books/{book_id}/social")).json()
        assert social["sealed_ahead"] == 1
        sealed = (await friend.get(f"/api/v1/books/{book_id}/reactions", params={"chunk": 2})).json()
        assert sealed["sealed"] == 1 and sealed["items"] == []

        # 친구가 읽고 있는 쪽(1쪽, 아직 검증 전)으로 와도 2쪽 본문은 여전히 봉인
        await friend.put(f"/api/v1/books/{book_id}/progress", json={"chunk_index": 2}, headers={"X-CSRF-Token": ft})
        for body in await _poll_all(friend, book_id, ft, 2):
            assert SECRET not in body

        # 2쪽까지 다 읽으면 폴링으로도 보인다
        for i in (1, 2):
            await read_chunk(friend, book_id, i, ft, clock)
        opened = (await friend.get(f"/api/v1/books/{book_id}/reactions", params={"chunk": 2})).json()
        assert [x["body"] for x in opened["items"]] == [SECRET]
        assert (await friend.get(f"/api/v1/books/{book_id}/social")).json()["sealed_ahead"] == 0


async def test_polling_endpoints_need_login_and_hide_other_circles(api, clock):
    async with api(upstream) as host, api(upstream) as friend, api(upstream) as stranger, api(upstream) as anon:
        book_id = await _race_pair(host, friend)
        ht = token_of((await host.get(f"/books/{book_id}")).text)
        for i in range(3):
            await read_chunk(host, book_id, i, ht, clock)
        await host.post(
            f"/api/v1/books/{book_id}/reactions",
            json={"chunk_index": 1, "anchor": 0, "comment": SECRET},
            headers={"X-CSRF-Token": ht},
        )
        for path in (f"/api/v1/books/{book_id}/social", f"/api/v1/books/{book_id}/reactions?chunk=1"):
            assert (await anon.get(path)).status_code == 401
        assert (await anon.put(f"/api/v1/books/{book_id}/presence", json={"chunk_index": 0})).status_code in (401, 403)

        # 같은 책을 다 읽은 낯선 사람 — 방이 달라 반응도 친구 위치도 보이지 않는다
        await signup(stranger, email="s@example.com", nickname="낯선이")
        st = token_of((await stranger.get(f"/books/{book_id}")).text)
        for i in range(3):
            await read_chunk(stranger, book_id, i, st, clock)
        bodies = await _poll_all(stranger, book_id, st, 1)
        assert all(SECRET not in b for b in bodies)
        assert all("호스트" not in b for b in bodies)


async def test_presence_poll_requires_csrf(api):
    async with api(upstream) as host, api(upstream) as friend:
        book_id = await _race_pair(host, friend)
        r = await friend.put(f"/api/v1/books/{book_id}/presence", json={"chunk_index": 0})
        assert r.status_code == 403


def test_broker_drops_empty_books_on_leave():
    b = Broker()
    ws1, ws2 = object(), object()
    b.join(1, 10, ws1)
    b.join(1, 11, ws2)
    b.leave(1, 10, ws1)
    assert 1 in b._subs
    b.leave(1, 11, ws2)
    assert b._subs == {}
    b.leave(2, 10, ws1)  # 모르는 책·사람은 조용히 넘어간다
    assert b._subs == {}


def test_reader_uses_one_live_channel_with_fallback():
    reader = (ROOT / "app/static/reader.js").read_text()
    presence = (ROOT / "app/static/presence.js").read_text()
    page = (ROOT / "app/templates/reader.html").read_text()
    assert "new WebSocket" not in reader and "haedokLive" in reader
    assert "function haedokLive" in presence
    assert page.index("/static/presence.js") < page.index("/static/reader.js")


@pytest.mark.skipif(shutil.which("node") is None, reason="node 가 없으면 건너뛴다")
def test_live_fallback_controller_in_node():
    r = subprocess.run(
        ["node", str(ROOT / "tests/js/live_fallback.test.cjs")], capture_output=True, text=True, timeout=60
    )
    assert r.returncode == 0, r.stdout + r.stderr
