"""리더: 쪽 이동은 읽음이 아니고, 다시 열면 '다 읽은 쪽 + 1'에서 이어 읽는다."""

import httpx

from app.services import pace
from tests.conftest import read_chunk, signup
from tests.test_api import upstream


async def _open(c) -> tuple[str, str, dict]:
    url = (await c.get("/open/wikisource/긴글")).headers["location"]
    token = (await c.get(url)).text.split('data-csrf="')[1].split('"')[0]
    return url, url.rsplit("/", 1)[1], {"X-CSRF-Token": token}


async def test_jumped_page_is_not_read_and_resume_is_verified_plus_one(api, clock):
    async with api(upstream) as c:
        await signup(c)
        url, book_id, h = await _open(c)
        await read_chunk(c, book_id, 0, h["X-CSRF-Token"], clock)
        # 마지막 쪽으로 건너뛰어 오래 머물러도 읽음이 아니다
        await c.put(f"/api/v1/books/{book_id}/progress", json={"chunk_index": 2}, headers=h)
        clock(10)
        jumped = await c.put(
            f"/api/v1/books/{book_id}/progress", json={"chunk_index": 2, "verified": True}, headers=h
        )
        reader = await c.get(url)
    assert jumped.json()["verified_chunk"] == 0 and jumped.json()["current_chunk"] == 2
    # 마지막으로 본 쪽(3쪽)이 아니라 다 읽은 다음 쪽(2쪽 = index 1)에서 연다
    assert 'data-start="1"' in reader.text and 'data-verified="0"' in reader.text


async def test_jumping_away_and_back_still_lets_you_read_in_order(api, clock):
    async with api(upstream) as c:
        await signup(c)
        _, book_id, h = await _open(c)
        await read_chunk(c, book_id, 0, h["X-CSRF-Token"], clock)
        await c.put(f"/api/v1/books/{book_id}/progress", json={"chunk_index": 2}, headers=h)
        r = await read_chunk(c, book_id, 1, h["X-CSRF-Token"], clock)
    assert r["verified_chunk"] == 1


async def test_fresh_book_starts_at_first_page(api):
    async with api(upstream) as c:
        await signup(c)
        url, _, _ = await _open(c)
        reader = await c.get(url)
    assert 'data-start="0"' in reader.text and 'id="jump"' in reader.text and 'id="back"' in reader.text


PAGES_HTML = "<p>" + ("가나다라 " * 230).strip() + "</p><p>" + "가" * 100 + "</p>"


def pages_upstream(req: httpx.Request) -> httpx.Response:
    if req.url.host == "ko.wikisource.org" and req.url.params.get("page") == "길고짧은글":
        return httpx.Response(200, json={"parse": {"title": "길고짧은글", "text": PAGES_HTML}})
    return upstream(req)


async def test_dwell_follows_the_page_length_and_server_decides(api, clock):
    async with api(pages_upstream) as c:
        await signup(c)
        url = (await c.get("/open/wikisource/길고짧은글")).headers["location"]
        token = (await c.get(url)).text.split('data-csrf="')[1].split('"')[0]
        book_id, h = url.rsplit("/", 1)[1], {"X-CSRF-Token": token}
        long, short = [(await c.get(f"/api/v1/books/{book_id}/chunks/{i}")).json() for i in (0, 1)]
        assert long["min_dwell_ms"] == pace.min_dwell_ms(long["text"]) == 55_200  # 920자
        assert short["min_dwell_ms"] == pace.min_dwell_ms(short["text"]) == 6_000  # 100자

        async def verify(i):
            r = await c.put(
                f"/api/v1/books/{book_id}/progress", json={"chunk_index": i, "verified": True}, headers=h
            )
            return r.json()["verified_chunk"]

        await c.put(f"/api/v1/books/{book_id}/progress", json={"chunk_index": 0}, headers=h)
        clock(2 / 60)  # 긴 쪽을 2초 만에 넘김
        skim = await verify(0)
        clock((long["min_dwell_ms"] - 3_000) / 60_000)  # 펼친 뒤 필요 시간 - 1초
        early = await verify(0)
        clock(2 / 60)
        ok = await verify(0)
        # 짧은 쪽은 6초면 된다
        await c.put(f"/api/v1/books/{book_id}/progress", json={"chunk_index": 1}, headers=h)
        clock(5 / 60)
        short_early = await verify(1)
        clock(1 / 60)
        short_ok = await verify(1)
    assert (skim, early, ok) == (-1, -1, 0)
    assert (short_early, short_ok) == (0, 1)
