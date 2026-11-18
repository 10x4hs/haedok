"""탐색 첫 화면의 표지 띠·오늘의 추천 — 누구나 아는 책 풀에서만, 하루 단위로 고정된 회전."""

import datetime as dt
import json
import re
from pathlib import Path
from urllib.parse import quote

from app import web
from tests.conftest import signup
from tests.test_api import upstream

DATA = Path(web.__file__).parent / "data"
DAY = dt.date(2026, 11, 18)


def _famous_ids() -> tuple[set, set]:
    pop = json.loads((DATA / "popular.json").read_text())
    free = {tuple(k) for k in pop["order"]} | {(b["source"], b["id"]) for b in pop["extra"]}
    store = set(json.loads((DATA / "home_picks.json").read_text())["store"]) | {
        b["id"] for b in web.BESTSELLERS
    }
    return free, store


def _href(b: dict) -> str:
    """화면의 카드 링크 모양(_ui.html catalog_card / 서점 카드)과 같게."""
    return f"/store/{b['id']}" if b.get("store") else f"/open/{b['source']}/{quote(b['id'], safe='/')}"


def test_pool_only_famous_ids_with_covers_and_no_duplicates():
    free_ids, store_ids = _famous_ids()
    free, store = web._famous_pool()
    assert len(free) >= 20 and len(store) >= 20
    assert all((b["source"], b["id"]) in free_ids and not b.get("store") for b in free)
    assert all(b["id"] in store_ids and b["store"] for b in store)
    keys = [web._title_key(b["title"]) for b in free + store]
    assert len(keys) == len(set(keys))  # 같은 책(제목)은 한 번만
    assert all(b.get("cover") for b in free + store)
    # 무료로 읽을 수 있는 고전이 서점 판보다 먼저 남는다(오만과 편견, 위대한 개츠비…)
    assert web._title_key("오만과 편견 (한글판)") in {web._title_key(b["title"]) for b in free}
    assert web._title_key("죄와 벌 1") == web._title_key("죄와 벌 (Crime and Punishment)")
    assert web._title_key("1984") == "1984"


def test_rows_are_deterministic_per_day_and_rotate():
    a1, p1 = web._famous_rows(DAY)
    a2, p2 = web._famous_rows(DAY)
    assert [b["title"] for b in a1] == [b["title"] for b in a2] and [b["title"] for b in p1] == [
        b["title"] for b in p2
    ]
    b1, q1 = web._famous_rows(DAY + dt.timedelta(days=1))
    assert [b["title"] for b in p1] != [b["title"] for b in q1]
    assert [b["title"] for b in a1] != [b["title"] for b in b1]


def test_strip_free_first_and_picks_never_repeat_strip():
    free_ids, store_ids = _famous_ids()
    for d in range(30):
        strip, picks = web._famous_rows(DAY + dt.timedelta(days=d))
        assert len(strip) == web.STRIP_FREE + web.STRIP_STORE and len(picks) == web.PICKS_SIZE
        kinds = [bool(b.get("store")) for b in strip]
        assert kinds == sorted(kinds)  # 바로 읽는 책 먼저
        assert any(kinds) and not all(kinds)  # 섞여 있다
        shown = {web._title_key(b["title"]) for b in strip}
        assert not shown & {web._title_key(b["title"]) for b in picks}
        for b in strip + picks:
            assert b.get("cover")
            if b.get("store"):
                assert b["id"] in store_ids and (b["id"] in web.STORE_INDEX or b["id"] in web.BEST_INDEX)
            else:
                assert (b["source"], b["id"]) in free_ids and (b["source"], b["id"]) in web.CATALOG_INDEX


def test_small_pool_fills_from_top_of_popular(monkeypatch):
    free, store = web._famous_pool()
    monkeypatch.setattr(web, "_famous_pool", lambda: (free[:9], store[:9]))
    strip, picks = web._famous_rows(DAY)
    assert len(picks) == web.PICKS_SIZE
    top = [web._title_key(web.CATALOG_INDEX[k]["title"]) for k in web.POPULAR_ORDER]
    for b in picks[2:]:  # 남은 두 권 뒤는 유명 고전 순서에서 채운다
        assert web._title_key(b["title"]) in top


async def test_discover_page_shows_famous_rows_with_valid_links(api, monkeypatch):
    monkeypatch.setattr(web, "_kst_today", lambda: DAY)
    strip, picks = web._famous_rows(DAY)
    async with api(upstream) as c:
        await signup(c)
        r = await c.get("/discover")
        assert r.status_code == 200
        html = r.text
        rail = html.split("오늘의 추천", 1)[1].split("</section>", 1)[0]
        hrefs = re.findall(r'<a class="card" href="([^"]+)"', rail)
        assert len(hrefs) == len(picks)
        assert hrefs == [_href(b) for b in picks]
        for b in picks:
            if b.get("store"):
                assert (await c.get(_href(b))).status_code == 200  # 서점 화면이 열린다
        marquee = html.split('class="marquee"', 1)[1].split("</div></div>", 1)[0]
        for b in strip:
            assert b["title"] in marquee


async def test_filtered_view_unchanged(api):
    async with api(upstream) as c:
        await signup(c)
        g = "단편소설"
        html = (await c.get("/discover", params={"g": g})).text
        cards = re.findall(r'<div data-q="', html)
        assert len(cards) == len(web.SHELVES[g])
        store = (await c.get("/discover", params={"g": "서점"})).text
        assert len(re.findall(r'<div data-q="', store)) == len(web.STORE)
