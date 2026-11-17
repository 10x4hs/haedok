"""홈 나선: 유명한 책(서점)을 정해 둔 순서로 걸고, 사이사이에 바로 읽는 고전을 섞는다."""

import json
import re

import httpx

from app import web
from tests.conftest import signup

SAPIENS, CARNEGIE = "310032628", "213576574"  # 사피엔스 · 데일 카네기 인간관계론


def test_home_picks_are_famous_store_books_in_the_curated_order():
    ids = json.loads((web.DATA / "home_picks.json").read_text())["store"]
    assert len(ids) == len(set(ids)) >= 20
    assert all(i in web.STORE_INDEX for i in ids)  # 서점에 없는 책을 가리키지 않는다
    assert [b["id"] for b in web.HOME_PICKS] == ids and all(b["store"] for b in web.HOME_PICKS)
    assert ids.index(SAPIENS) < 5 and ids.index(CARNEGIE) < 5
    titles = [web.STORE_INDEX[i]["title"] for i in ids[:12]]
    for classic in ("폭풍의 언덕", "햄릿", "참을 수 없는 존재의 가벼움"):  # 민음사 세계문학 고전이 앞쪽에
        assert classic in titles


def test_drum_interleaves_one_free_book_after_every_two_store_books():
    free = [{"source": "wikisource", "id": f"글{n}", "title": f"글{n}"} for n in range(web.HOME_FREE)]
    drum = web._drum(free)
    kinds = "".join("S" if b.get("store") else "F" for b in drum)
    assert "SSS" not in kinds and "FFF" not in kinds  # 한 종류가 몰려 있지 않다
    assert kinds.startswith("SSFSSF")
    assert [b for b in drum if not b.get("store")] == free  # 인기 순서는 그대로
    assert [b["id"] for b in drum if b.get("store")] == [b["id"] for b in web.HOME_PICKS]
    assert 36 <= len(drum) <= 44
    assert web._drum([]) == web.HOME_PICKS  # 읽을 책이 없어도 나선은 선다


async def test_home_helix_shows_about_forty_books_without_duplicates(api):
    async with api(lambda r: httpx.Response(200, json={})) as c:
        await signup(c)
        home = await c.get("/")
    assert home.status_code == 200
    links = re.findall(r'<a class="hx-book" href="([^"]+)"', home.text)
    store = [h for h in links if h.startswith("/store/")]
    free = [h for h in links if h.startswith("/open/")]
    assert len(links) == len(set(links)) == len(store) + len(free)  # 같은 책이 두 번 걸리지 않는다
    assert 36 <= len(links) <= 44
    assert len(store) >= 20 and len(free) >= 10
    assert f"/store/{SAPIENS}" in links[:8] and f"/store/{CARNEGIE}" in links[:8]
    kinds = "".join("S" if h.startswith("/store/") else "F" for h in links)
    assert "SSS" not in kinds
    # 서점 책은 모두 실제 표지가 있고, 누르면 그 책의 서점 화면이 열린다
    assert all(web.STORE_INDEX[h.removeprefix("/store/")]["cover"] for h in store)
