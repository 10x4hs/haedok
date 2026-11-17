"""홈 나선: 누구나 아는 책(서점)을 정해 둔 순서로 걸고, 바로 읽는 고전 몇 권을 맨 뒤에 붙인다."""

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
    assert len(ids) == 40  # 나선에 보이는 책은 모두 유명한 책


def test_drum_puts_famous_books_first_and_free_classics_last():
    free = [{"source": "wikisource", "id": f"글{n}", "title": f"글{n}"} for n in range(web.HOME_FREE)]
    drum = web._drum(free)
    assert drum[: len(web.HOME_PICKS)] == web.HOME_PICKS and drum[len(web.HOME_PICKS) :] == free
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
    assert len(store) == 40 and 1 <= len(free) <= 4
    assert f"/store/{SAPIENS}" in links[:8] and f"/store/{CARNEGIE}" in links[:8]
    assert all(h.startswith("/store/") for h in links[:40])  # 앞쪽은 모두 유명한 책
    # 서점 책은 모두 실제 표지가 있고, 누르면 그 책의 서점 화면이 열린다
    assert all(web.STORE_INDEX[h.removeprefix("/store/")]["cover"] for h in store)
