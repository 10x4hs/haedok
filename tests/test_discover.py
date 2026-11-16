"""탐색: 예상 시간 · 분위기 칩 · 친구들이 읽는 책 · 책 소개 · 검색(서점 포함)."""

import re
from urllib.parse import quote

import httpx

from app.services import discovery
from app.web import CATALOG, CATALOG_INDEX, POPULAR_ORDER, STORE
from tests.conftest import signup
from tests.test_api import upstream


def csrf(html: str) -> str:
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def test_time_label_rounds_coarser_as_books_get_longer():
    assert discovery.time_label(1) == "약 1분"
    assert discovery.time_label(7) == "약 7분"
    assert discovery.time_label(23) == "약 25분"
    assert discovery.time_label(60) == "약 1시간"
    assert discovery.time_label(98) == "약 1시간 40분"
    assert discovery.time_label(725) == "약 12시간"


def test_reading_speed_depends_on_language():
    assert discovery.minutes_for(5500, "ko") == 10
    assert discovery.minutes_for(13000, None) == 10
    assert discovery.pages_for(2401, "ko") == 3  # 한국어 1쪽 = 1,200자


def test_enrich_fills_missing_lengths_with_genre_median():
    books = [
        {"source": "s", "id": "a", "genre": "시", "lang": "ko"},
        {"source": "s", "id": "b", "genre": "시", "lang": "ko"},
        {"source": "s", "id": "c", "genre": "시", "lang": "ko"},
        {"source": "s", "id": "d", "genre": "희곡", "lang": "ko"},
    ]
    discovery.enrich(books, {"s:a": 1100, "s:b": 3300})
    assert [b["chars"] for b in books] == [1100, 3300, 2200, discovery.FALLBACK_CHARS]
    assert books[0]["time"] == "약 2분" and books[0]["pages"] == 1


def test_every_catalog_book_has_a_time_estimate():
    assert all(b["time"].startswith("약 ") and b["minutes"] >= 1 and b["pages"] >= 1 for b in CATALOG)
    # 짧은 책 줄이 비지 않을 만큼 30분 안쪽 책이 있다
    assert sum(b["minutes"] <= 30 for b in CATALOG) >= 14
    # 출판사 표지는 쓰지 않는다: 표지는 직접 그린 홈 표지뿐
    assert all(b["cover"] is None or b["cover"].startswith("/static/covers/home/") for b in CATALOG)


async def test_mood_chip_filters_server_side(api):
    short = discovery.mood_books(CATALOG, "short", POPULAR_ORDER)
    async with api(upstream) as c:
        await signup(c)
        page = await c.get("/discover", params={"mood": "short"})
        poems = await c.get("/discover", params={"mood": "poem"})
        unknown = await c.get("/discover", params={"mood": "nope"})
    assert page.status_code == 200 and page.text.count('class="card"') == len(short) > 50
    assert all(b["minutes"] <= 30 for b in short)
    assert 'aria-current="page"' in page.text and "30분이면 다 읽는 책" in page.text
    assert all(b["genre"] in {"시", "시집", "세계 · 시"} for b in discovery.mood_books(CATALOG, "poem", POPULAR_ORDER))
    assert poems.text.count('class="card"') == len(discovery.mood_books(CATALOG, "poem", POPULAR_ORDER))
    assert "오늘 시작하기 좋은 짧은 책" in unknown.text  # 모르는 분위기는 탐색 첫 화면


async def test_explore_has_search_entry_chips_and_curation_rows(api):
    async with api(upstream) as c:
        await signup(c)
        page = await c.get("/discover")
    html = page.text
    assert 'class="d-search" href="/search"' in html
    for key in discovery.CHIP_MOODS:
        assert f'href="/discover?mood={key}"' in html
    assert "한국 문학 대표작" in html and "세계 고전" in html and html.count("전체 보기") >= 4
    assert "친구들이 읽는 책" not in html  # 친구가 없으면 줄을 숨긴다
    assert "/static/css/discover.css" in html


async def test_friends_reading_row_detail_and_avatars(api):
    book = CATALOG_INDEX[("wikisource", "봄봄")]
    detail_url = f"/book/wikisource/{quote('봄봄')}"
    async with api(upstream) as host, api(upstream) as friend:
        await signup(host, email="host@example.com", nickname="호스트")
        await signup(friend, email="friend@example.com", nickname="민지")
        page = await host.get("/races")
        r = await host.post("/races/start/wikisource/봄봄", data={"csrf": csrf(page.text), "days": "7"})
        race = await host.get(r.headers["location"])
        code = re.search(r'id="code">([A-Z0-9]{6})<', race.text).group(1)
        preview = await friend.get(f"/join/{code}")
        await friend.post(f"/join/{code}", data={"csrf": csrf(preview.text)})
        explore = await host.get("/discover")
        detail = await host.get(detail_url)
        lonely = await friend.get(f"/book/wikisource/{quote('운수 좋은 날')}")
    assert "친구들이 읽는 책" in explore.text and 'aria-label="읽는 친구 1명"' in explore.text
    assert detail.status_code == 200 and book["title"] in detail.text
    main = detail.text.split('<main', 1)[1].split("</main>", 1)[0]
    assert main.count("btn primary") == 1 and "읽기 시작" in main
    assert f'href="/open/wikisource/{quote("봄봄")}"' in detail.text
    assert f'action="/races/start/wikisource/{quote("봄봄")}"' in detail.text and "친구와 대결" in detail.text
    assert "민지" in detail.text and book["time"].removeprefix("약 ") in detail.text
    assert "아직 이 책을 읽는 친구가 없어요" in lonely.text


async def test_friends_ignore_uploaded_books_and_strangers(api, session_factory):
    from app.models import User

    async with session_factory() as s:
        s.add(User(nickname="혼자"))
        await s.commit()
        me = (await s.execute(User.__table__.select())).first()
        assert await discovery.friends_reading(s, me.id) == {}


async def test_book_detail_404_and_cards_link_to_detail(api):
    async with api(upstream) as c:
        await signup(c)
        missing = await c.get("/book/wikisource/없는책")
        grid = await c.get("/discover", params={"g": "동화"})
    assert missing.status_code == 404
    assert 'href="/book/wikisource/' in grid.text and 'href="/open/' not in grid.text


def nothing_upstream(req: httpx.Request) -> httpx.Response:
    if req.url.host == "ko.wikisource.org":
        return httpx.Response(200, json={"query": {"search": []}})
    return upstream(req)


async def test_search_finds_store_books_case_insensitively(api):
    item = STORE[0]
    async with api(nothing_upstream) as c:
        await signup(c)
        store = await c.get("/search", params={"q": item["title"][:2]})
        lower = await c.get("/search", params={"q": "austen"})
        empty = await c.get("/search", params={"q": "ㅋㅋㅋ없는책"})
        blank = await c.get("/search")
    assert "서점에서 찾음" in store.text and f'href="/store/{item["id"]}"' in store.text
    assert "서가에서 찾음" in lower.text and "Austen" in lower.text
    assert "맞는 책을 찾지 못했어요" in empty.text and "많이 찾는 작가" in empty.text
    assert blank.status_code == 200 and "autofocus" in blank.text and "검색 결과" not in blank.text
