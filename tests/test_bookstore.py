"""서점 검색(app/services/bookstore.py)과 /search · /store/{id} · /upload?store= 연결."""

import asyncio
from pathlib import Path

import httpx
import pytest

from app.core.config import Settings, get_settings
from app.services import bookstore
from tests.conftest import mock_client, signup
from tests.test_api import upstream

FIXTURES = Path(__file__).parent / "fixtures"
SEARCH_HTML = (FIXTURES / "aladin_search_ebook.html").read_text(encoding="utf-8")
PRODUCT_HTML = (FIXTURES / "aladin_product_ebook.html").read_text(encoding="utf-8")
PRODUCT_ID = "406573415"  # 한반도 사피엔스 — 고른 책(store.json)에 없는 책


@pytest.fixture(autouse=True)
def _aladin_page_mode(monkeypatch):
    """이 파일의 기존 테스트는 '알라딘 화면 읽기'(BOOKSTORE_LIVE=true, 네이버 키 없음) 방식을 본다.
    기본값은 꺼짐이고, 네이버 방식은 아래 '네이버 책 검색' 테스트가 따로 켠다."""
    monkeypatch.setenv("BOOKSTORE_LIVE", "true")
    monkeypatch.delenv("NAVER_CLIENT_ID", raising=False)
    monkeypatch.delenv("NAVER_CLIENT_SECRET", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class Aladin:
    """알라딘 흉내. 받은 요청을 적어 두고, 검색·상품 화면을 돌려준다."""

    def __init__(self, search=SEARCH_HTML, product=PRODUCT_HTML, status=200, fail_first=0, exc=None):
        self.search, self.product, self.status = search, product, status
        self.fail_first, self.exc = fail_first, exc
        self.calls: list[httpx.Request] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        if req.url.host != "www.aladin.co.kr":
            return upstream(req)
        self.calls.append(req)
        if self.exc:
            raise self.exc
        if len(self.calls) <= self.fail_first:
            return httpx.Response(503)
        if self.status != 200:
            return httpx.Response(self.status, headers={"location": "https://evil.example/"})
        if req.url.path == "/search/wsearchresult.aspx":
            return httpx.Response(200, text=self.search)
        if req.url.path == "/shop/wproduct.aspx" and req.url.params.get("ItemId") == PRODUCT_ID:
            return httpx.Response(200, text=self.product)
        return httpx.Response(200, text="<html><head><title>알라딘</title></head></html>")


# --- 파서 ---


def test_parses_real_search_result_markup():
    items = bookstore.parse_search_page(SEARCH_HTML)
    assert [i["id"] for i in items] == ["310032628", "406573415", "310032635"]
    assert items[0] == {
        "id": "310032628",
        "title": "사피엔스",
        "author": "유발 하라리",  # 옮긴이·감수는 지은이가 아니다
        "cover": "https://image.aladin.co.kr/product/31003/26/cover500/e412531178_1.jpg",
        "price": 15750,
        "url": "https://www.aladin.co.kr/shop/wproduct.aspx?ItemId=310032628&start=pebook",
        "publisher": "김영사",
        "pubdate": "2023년 1월",
        "description": None,
    }
    assert items[1]["author"] == "박정재, 이진옥"  # 지은이가 둘


def test_search_parser_handles_translator_only_and_old_price_markup():
    page = (
        '<div class="ss_book_box" itemId="111"><a class="bo3">향수 &amp; 이야기</a>'
        '<a href="/Search/wSearchResult.aspx?AuthorSearch=강명순@1">강명순</a> (옮긴이)'
        '<span class="ss_p2"><em>9,100</em>원</span></div>'
        '<div class="ss_book_box" itemId="222"><img src="https://evil.example/cover/x.jpg"></div>'
    )
    assert bookstore.parse_search_page(page) == [
        {
            "id": "111",
            "title": "향수 & 이야기",
            "author": None,
            "cover": None,
            "price": 9100,
            "url": "https://www.aladin.co.kr/shop/wproduct.aspx?ItemId=111&start=pebook",
            "publisher": None,
            "pubdate": None,
            "description": None,
        }
    ]  # 제목 없는 칸(222)은 버린다
    assert bookstore.parse_search_page("<html>검색 결과가 없습니다</html>") == []


def test_parses_real_product_page_and_rejects_non_ebooks():
    item = bookstore.parse_product_page(PRODUCT_HTML, PRODUCT_ID)
    assert item["title"] == "한반도 사피엔스" and item["author"] == "이진옥, 박정재"
    assert item["cover"] == "https://image.aladin.co.kr/product/40657/34/cover500/e742639054_e225.jpg"
    assert item["price"] == 14000 and item["publisher"] == "김영사" and item["pubdate"] == "2026-11-10"
    assert item["url"].endswith(f"ItemId={PRODUCT_ID}&start=pebook")
    assert item["description"].startswith("빙하기")
    paper = PRODUCT_HTML.replace("[전자책] 한반도 사피엔스 : 알라딘", "한반도 사피엔스 : 알라딘")
    assert bookstore.parse_product_page(paper, PRODUCT_ID) is None
    assert bookstore.parse_product_page("<html></html>", PRODUCT_ID) is None
    # 표지는 알라딘 이미지 서버 주소만 받는다
    other = PRODUCT_HTML.replace("https://image.aladin.co.kr/product", "https://evil.example/product")
    assert bookstore.parse_product_page(other, PRODUCT_ID)["cover"] is None


# --- 고른 책 안에서 찾기 · 합치기 ---


def test_local_search_and_merge_put_curated_books_first():
    curated = [
        {"id": "1", "title": "Sapiens 사피엔스", "author": "유발 하라리"},
        {"id": "2", "title": "데미안", "author": "헤르만 헤세"},
    ]
    assert bookstore.local_search(curated, "SAPIENS") == [curated[0]]
    assert bookstore.local_search(curated, "헤세  데미안") == [curated[1]]  # 낱말이 모두 들어 있으면 된다
    assert bookstore.local_search(curated, "민음사 데미안") == []
    assert bookstore.local_search(curated, " ") == []
    live = [{"id": "9", "title": "새 책"}, {"id": "2", "title": "데미안(알라딘)"}, {"id": "1", "title": "x"}]
    merged = bookstore.merge([curated[0]], live, {b["id"]: b for b in curated})
    assert [b["id"] for b in merged] == ["1", "2", "9"] and merged[1] is curated[1]
    assert len(bookstore.merge([], live, {}, limit=2)) == 2


# --- 조회 · 캐시 ---


async def test_search_fetches_fixed_host_once_and_caches_for_ten_minutes(monkeypatch):
    now = {"t": 1000.0}
    monkeypatch.setattr(bookstore, "_clock", lambda: now["t"])
    aladin = Aladin()
    async with mock_client(aladin) as client:
        items = await bookstore.search(client, " 사피엔스 ")
        again = await bookstore.search(client, "사피엔스", limit=2)
        assert len(items) == 3 and [i["id"] for i in again] == ["310032628", "406573415"]
        assert len(aladin.calls) == 1
        url = aladin.calls[0].url
        assert (url.scheme, url.host, url.path) == ("https", "www.aladin.co.kr", "/search/wsearchresult.aspx")
        assert dict(url.params) == {"SearchTarget": "eBook", "SearchWord": "사피엔스"}
        # 검색에서 본 책은 상품 화면을 다시 읽지 않는다
        assert (await bookstore.lookup(client, "310032628"))["title"] == "사피엔스"
        assert len(aladin.calls) == 1
        now["t"] += bookstore.SEARCH_TTL_S + 1
        await bookstore.search(client, "사피엔스")
    assert len(aladin.calls) == 2


async def test_lookup_reads_product_page_and_caches(monkeypatch):
    now = {"t": 0.0}
    monkeypatch.setattr(bookstore, "_clock", lambda: now["t"])
    aladin = Aladin()
    async with mock_client(aladin) as client:
        item = await bookstore.lookup(client, PRODUCT_ID)
        assert item["title"] == "한반도 사피엔스"
        assert dict(aladin.calls[0].url.params) == {"ItemId": PRODUCT_ID}
        now["t"] += 3600
        assert await bookstore.lookup(client, PRODUCT_ID) == item and len(aladin.calls) == 1
        # 전자책이 아닌 번호는 '없음'으로 기억한다
        assert await bookstore.lookup(client, "777") is None
        assert await bookstore.lookup(client, "777") is None
        assert len(aladin.calls) == 2
        # 모양이 다른 id는 알라딘에 묻지도 않는다
        for bad in ("", "0", "abc", "12a", "-1", "1234567890123", "1/../x", "１２３"):
            assert await bookstore.lookup(client, bad) is None
        assert len(aladin.calls) == 2
        now["t"] += bookstore.LOOKUP_TTL_S
        await bookstore.lookup(client, PRODUCT_ID)
    assert len(aladin.calls) == 3


async def test_failures_return_empty_retry_once_and_are_remembered_briefly(monkeypatch):
    now = {"t": 0.0}
    monkeypatch.setattr(bookstore, "_clock", lambda: now["t"])
    flaky = Aladin(fail_first=1)
    async with mock_client(flaky) as client:
        assert len(await bookstore.search(client, "사피엔스")) == 3  # 503 한 번 → 다시 해서 성공
    assert len(flaky.calls) == 2

    bookstore.clear_cache()
    down = Aladin(fail_first=99)
    async with mock_client(down) as client:
        assert await bookstore.search(client, "사피엔스") == []
        assert await bookstore.lookup(client, PRODUCT_ID) is None
        assert len(down.calls) == 4  # 요청마다 재시도는 한 번뿐
        assert await bookstore.search(client, "사피엔스") == []
        assert len(down.calls) == 4  # 잠깐은 다시 묻지 않는다
        now["t"] += bookstore.FAILURE_TTL_S + 1
        await bookstore.search(client, "사피엔스")
        assert len(down.calls) == 6

    bookstore.clear_cache()
    broken = Aladin(exc=httpx.ConnectError("no route"))
    async with mock_client(broken) as client:
        assert await bookstore.search(client, "사피엔스") == []
    assert len(broken.calls) == 2

    bookstore.clear_cache()
    redirecting = Aladin(status=302)  # 다른 호스트로 보내도 따라가지 않는다
    async with mock_client(redirecting) as client:
        assert await bookstore.lookup(client, PRODUCT_ID) is None
    assert len(redirecting.calls) == 1


async def test_slow_upstream_is_cut_at_the_time_budget(monkeypatch):
    monkeypatch.setattr(bookstore, "BUDGET_S", 0.05)

    async def slow(req: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200, text=SEARCH_HTML)

    async with mock_client(slow) as client:
        started = asyncio.get_running_loop().time()
        assert await bookstore.search(client, "사피엔스") == []
        assert asyncio.get_running_loop().time() - started < 1


async def test_live_fetching_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("BOOKSTORE_LIVE", "false")
    assert Settings().bookstore_live is False
    assert Settings.model_fields["bookstore_live"].default is False  # 알라딘 화면 읽기는 기본으로 꺼져 있다
    monkeypatch.setattr(bookstore, "get_settings", lambda: Settings(bookstore_live=False))
    aladin = Aladin()
    async with mock_client(aladin) as client:
        assert await bookstore.search(client, "사피엔스") == []
        assert await bookstore.lookup(client, PRODUCT_ID) is None
    assert aladin.calls == []


def test_cache_is_bounded_and_drops_least_recently_used():
    cache = bookstore._TTLCache(2)
    cache.put("a", 1, 60)
    cache.put("b", 2, 60)
    assert cache.get("a") == (True, 1)
    cache.put("c", 3, 60)
    assert len(cache) == 2 and cache.get("b") == (False, None) and cache.get("a") == (True, 1)


# --- 화면 ---


async def test_search_page_shows_store_section_with_curated_books_first(api):
    from app.web import STORE

    curated = bookstore.local_search(STORE, "사피엔스")
    assert curated  # 고른 책에 사피엔스가 있다
    aladin = Aladin()
    async with api(aladin) as c:
        await signup(c)
        r = await c.get("/search", params={"q": "사피엔스"})
        classic = await c.get("/search", params={"q": "봄봄"})
    assert r.status_code == 200 and "<h2>서점</h2>" in r.text
    section = r.text.split("<h2>서점</h2>")[1].split("</section>")[0]
    links = [part.split('"')[0] for part in section.split('href="/store/')[1:]]
    assert links[: len(curated)] == [b["id"] for b in curated]
    assert PRODUCT_ID in links and len(links) == len(set(links))
    assert "한반도 사피엔스" in section and 'class="mono">서점</em>' in section
    assert "/cover200/e742639054_e225.jpg" in section  # 목록에는 작은 표지
    # 기존 구역은 그대로, 서점보다 위에 있다
    assert "서가에서 찾음" in classic.text
    assert "<h2>서점</h2>" not in classic.text.split("서가에서 찾음")[0]


async def test_dynamic_store_page_renders_like_a_curated_one_and_bad_ids_are_404(api):
    aladin = Aladin()
    async with api(aladin) as c:
        await signup(c)
        page = await c.get(f"/store/{PRODUCT_ID}")
        again = await c.get(f"/store/{PRODUCT_ID}")
        calls = len(aladin.calls)
        unknown = await c.get("/store/777")  # 알라딘에 전자책으로 없는 번호
        invalid = [await c.get(f"/store/{bad}") for bad in ("0", "abc", "1234567890123", "12-3")]
    assert page.status_code == 200 and again.status_code == 200 and calls == 1
    assert "한반도 사피엔스" in page.text and "이진옥, 박정재" in page.text and "14,000원" in page.text
    assert f"wproduct.aspx?ItemId={PRODUCT_ID}&amp;start=pebook" in page.text
    assert f'href="/upload?store={PRODUCT_ID}"' in page.text and "전자책 · 알라딘" in page.text
    assert unknown.status_code == 404 and all(r.status_code == 404 for r in invalid)
    assert len(aladin.calls) == calls + 1  # 모양이 다른 id는 알라딘에 묻지 않았다


async def test_upload_for_a_dynamic_store_book_attaches_its_cover(api):
    aladin = Aladin()
    async with api(aladin) as c:
        await signup(c)
        form = await c.get("/upload", params={"store": PRODUCT_ID})
        token = form.text.split('name="csrf" value="')[1].split('"')[0]
        up = await c.post(
            "/upload",
            files={"file": ("mybook.txt", ("가나다라 마바사 " * 30).encode(), "text/plain")},
            data={"csrf": token, "store": PRODUCT_ID},
        )
        library = await c.get("/library")
    assert "이 책의 파일을 올려요" in form.text and "한반도 사피엔스" in form.text
    assert f'name="store" value="{PRODUCT_ID}"' in form.text
    assert up.status_code == 303 and "e742639054_e225.jpg" in library.text
    assert len(aladin.calls) == 1  # 올릴 때는 캐시에서 꺼낸다


async def test_bookstore_failure_never_breaks_search(api, monkeypatch):
    broken = (Aladin(exc=httpx.ConnectError("down")), Aladin(status=403), Aladin(search="<html>차단</html>"))
    for n, aladin in enumerate(broken):
        bookstore.clear_cache()
        async with api(aladin) as c:
            await signup(c, email=f"u{n}@example.com")
            curated = await c.get("/search", params={"q": "데미안"})
            nothing = await c.get("/search", params={"q": "없는책zzz"})
            classic = await c.get("/search", params={"q": "봄봄"})
        assert curated.status_code == 200 and "<h2>서점</h2>" in curated.text  # 고른 책은 그대로 나온다
        assert nothing.status_code == 200 and "<h2>서점</h2>" not in nothing.text
        assert classic.status_code == 200 and "서가에서 찾음" in classic.text

    def boom(*args, **kwargs):
        raise RuntimeError("parser bug")

    monkeypatch.setattr(bookstore, "parse_search_page", boom)
    bookstore.clear_cache()
    async with api(Aladin()) as c:
        await signup(c, email="bug@example.com")
        r = await c.get("/search", params={"q": "데미안"})
    assert r.status_code == 200 and "<h2>서점</h2>" in r.text


async def test_rate_limited_user_falls_back_to_curated_results(api, monkeypatch):
    from app import web

    monkeypatch.setattr(web, "STORE_SEARCH_LIMIT", 1)
    monkeypatch.setattr(web, "STORE_LOOKUP_LIMIT", 1)
    aladin = Aladin()
    async with api(aladin) as c:
        await signup(c)
        first = await c.get("/search", params={"q": "사피엔스"})
        cached = await c.get("/search", params={"q": "사피엔스"})  # 캐시에 있는 검색은 한도를 쓰지 않는다
        limited = await c.get("/search", params={"q": "데미안"})
        assert len(aladin.calls) == 1
        ok = await c.get(f"/store/{PRODUCT_ID}")  # 검색에서 본 책은 캐시에서 열린다
        assert len(aladin.calls) == 1
        one = await c.get("/store/777")
        blocked = await c.get("/store/778")
    assert "한반도 사피엔스" in first.text and "한반도 사피엔스" in cached.text
    assert limited.status_code == 200 and "<h2>서점</h2>" in limited.text and "데미안" in limited.text
    assert ok.status_code == 200 and one.status_code == 404 and blocked.status_code == 404
    assert len(aladin.calls) == 2  # 778은 한도를 넘어 알라딘에 묻지 않았다


async def test_search_survives_a_broken_rate_limit_store(api, monkeypatch):
    from app.core import ratelimit

    async def broken(*args, **kwargs):
        raise RuntimeError("db down")

    aladin = Aladin()
    async with api(aladin) as c:
        await signup(c)
        monkeypatch.setattr(ratelimit, "check", broken)
        r = await c.get("/search", params={"q": "사피엔스"})
        missing = await c.get(f"/store/{PRODUCT_ID}")
    assert r.status_code == 200 and "<h2>서점</h2>" in r.text  # 고른 책만 보여 준다
    assert missing.status_code == 404 and aladin.calls == []  # 한도를 못 세면 알라딘에 묻지 않는다


# --- 네이버 책 검색 (공식 API) ---

NAVER_JSON = {
    "items": [
        {
            "title": "<b>사피엔스</b> (유인원에서 사이보그까지)",
            "link": "https://search.shopping.naver.com/book/catalog/1",
            "image": "https://shopping-phinf.pstatic.net/main_1/1.jpg",
            "author": "유발 하라리^조현욱",
            "discount": "24120",
            "publisher": "김영사",
            "pubdate": "20230401",
            "isbn": "9788934972464",
            "description": "인류의 <b>역사</b>를 다시 쓴 책",
        },
        {"title": "ISBN 없는 책", "isbn": "", "image": "", "author": ""},
    ]
}


class Naver:
    def __init__(self, status=200):
        self.status, self.calls = status, []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        if req.url.host == "www.aladin.co.kr":
            raise AssertionError("네이버 키가 있으면 알라딘 화면을 읽지 않는다")
        if req.url.host != "openapi.naver.com":
            return upstream(req)
        self.calls.append(req)
        return httpx.Response(self.status, json=NAVER_JSON if self.status == 200 else {"errorCode": "024"})


@pytest.fixture
def naver_keys(monkeypatch):
    monkeypatch.setenv("BOOKSTORE_LIVE", "false")
    monkeypatch.setenv("NAVER_CLIENT_ID", "id-x")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "secret-x")
    get_settings.cache_clear()


def test_nothing_is_fetched_by_default(monkeypatch):
    monkeypatch.delenv("BOOKSTORE_LIVE", raising=False)
    get_settings.cache_clear()
    assert bookstore.provider() is None and not bookstore.live_enabled()


async def test_naver_search_and_lookup_by_isbn(naver_keys):
    assert bookstore.provider() == "naver"
    naver = Naver()
    async with mock_client(naver) as client:
        items = await bookstore.search(client, "사피엔스")
        again = await bookstore.search(client, "사피엔스")  # 캐시
        book = await bookstore.lookup(client, "9788934972464")
        missing = await bookstore.lookup(client, "9780000000002")
    assert [i["id"] for i in items] == ["9788934972464"] and again == items
    it = items[0]
    assert it["title"] == "사피엔스 (유인원에서 사이보그까지)" and it["author"] == "유발 하라리, 조현욱"
    assert it["price"] == 24120 and it["publisher"] == "김영사" and it["cover"].startswith("https://")
    assert it["url"].startswith("https://www.aladin.co.kr/search/wsearchresult.aspx?SearchTarget=EBook")
    assert book == it and missing is None
    first = naver.calls[0]
    assert first.headers["X-Naver-Client-Id"] == "id-x" and first.headers["X-Naver-Client-Secret"] == "secret-x"
    assert len(naver.calls) == 2  # 검색 1번 + 없는 ISBN 조회 1번 (있는 책은 검색 결과가 캐시에 있다)


async def test_naver_failure_returns_empty(naver_keys):
    async with mock_client(Naver(status=401)) as client:
        assert await bookstore.search(client, "사피엔스") == []


async def test_search_page_and_store_page_work_with_naver(api, naver_keys):
    async with api(Naver()) as c:
        await signup(c)
        r = await c.get("/search", params={"q": "사피엔스"})
        page = await c.get("/store/9788934972464")
        bad = await c.get("/store/9781234")
    assert r.status_code == 200 and 'href="/store/9788934972464"' in r.text
    assert page.status_code == 200 and "사피엔스 (유인원에서 사이보그까지)" in page.text
    assert "SearchTarget=EBook" in page.text and bad.status_code == 404

