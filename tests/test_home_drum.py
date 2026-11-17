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


async def test_home_helix_shows_bestsellers_then_famous_books_without_duplicates(api):
    async with api(lambda r: httpx.Response(200, json={})) as c:
        await signup(c)
        home = await c.get("/")
        best_page = await c.get(f"/store/{web.BESTSELLERS[0]['id']}")
    assert home.status_code == 200
    links = re.findall(r'<a class="hx-book" href="([^"]+)"', home.text)
    store = [h for h in links if h.startswith("/store/")]
    free = [h for h in links if h.startswith("/open/")]
    assert len(links) == len(set(links)) == len(store) + len(free)  # 같은 책이 두 번 걸리지 않는다
    # 맨 앞 세 권은 지금의 베스트셀러, 순위 배지가 붙고 서점 화면으로 열린다
    assert links[:3] == [f"/store/{b['id']}" for b in web.BESTSELLERS[:3]]
    assert home.text.count('data-best="1"') == 1 and 'data-best="3"' in home.text
    assert best_page.status_code == 200 and web.BESTSELLERS[0]["title"] in best_page.text
    assert 40 <= len(store) <= 43 and 1 <= len(free) <= 4
    assert f"/store/{SAPIENS}" in links[:8] and f"/store/{CARNEGIE}" in links[:8]
    assert all(h.startswith("/store/") for h in links[:40])  # 앞쪽은 모두 유명한 책


def test_drum_drops_a_pick_that_is_already_a_bestseller():
    same = {"id": "y1", "title": web.HOME_PICKS[0]["title"] + " (리커버 특별판)", "cover": "https://x/y.jpg"}
    drum = web._drum([], [same, {"id": "y2", "title": "다른 책", "cover": "https://x/z.jpg"}])
    assert [b["id"] for b in drum[:2]] == ["y1", "y2"] and [b["best"] for b in drum[:2]] == [1, 2]
    assert web.HOME_PICKS[0]["id"] not in [b["id"] for b in drum]  # 같은 책은 베스트셀러 자리 하나만
    assert len(drum) == len(web.HOME_PICKS) + 1
