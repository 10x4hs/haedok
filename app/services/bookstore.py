"""서점 검색 — 고른 130권(app/data/store.json) 밖의 책도 같은 서점 화면으로 연다.

StoreItem은 store.json 한 권과 같은 모양(id·title·author·cover·price·url)에 출판사·출간일·소개가 더 붙는다.
id는 알라딘 ItemId다.

실시간 조회는 알라딘 웹 페이지를 읽는다 (키 없음):
  검색  https://www.aladin.co.kr/search/wsearchresult.aspx?SearchTarget=eBook&SearchWord=…
  상품  https://www.aladin.co.kr/shop/wproduct.aspx?ItemId=…  (og:title·og:image·og:price)

알라딘 공식 OpenAPI(TTB 키)는 쓰지 않는다 — 2026-10-30에 서비스가 끝났다(새 키 발급은 2026-09-04 종료).
  공지: https://blog.aladin.co.kr/openapi/popup/6695306
  매뉴얼(ItemSearch·ItemLookUp 인자): https://docs.google.com/document/d/1mX-WxuoGs8Hy-QalhHcvuV17n50uGI2Sg_GHofgiePE
  지금은 어떤 키로 불러도 errorCode 999 "OpenAPI서비스는 종료되었습니다"가 돌아온다.
알라딘 robots.txt(2026-10-13)는 이름이 없는 자동 수집기에 `Disallow: /`를 건다. 그래서 실시간 조회는
BOOKSTORE_LIVE=false 로 끌 수 있고, 꺼도 고른 책 안에서는 검색이 된다(local_search).

지키는 것:
- 주소는 아래 두 상수뿐이다. 사용자가 준 값은 쿼리 인자로만 들어가고, 리다이렉트는 따라가지 않는다(SSRF 없음).
- 한 번 부르는 데 재시도 1번까지 합쳐 BUDGET_S 초를 넘지 않는다.
- 결과는 메모리에 둔다(검색 10분·상품 24시간, 개수 상한). 실패도 잠깐 기억해 같은 요청을 되풀이하지 않는다.
- 밖으로 예외를 내지 않는다. 실패하면 [] / None을 돌려주고 로그만 남긴다.
"""

import asyncio
import html as htmllib
import logging
import re
import time
from collections import OrderedDict
from collections.abc import Iterable
from typing import Any, TypedDict
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from app.core.config import get_settings

log = logging.getLogger("haedok.bookstore")

SEARCH_URL = "https://www.aladin.co.kr/search/wsearchresult.aspx"
PRODUCT_URL = "https://www.aladin.co.kr/shop/wproduct.aspx"
COVER_HOST = "https://image.aladin.co.kr/"

ATTEMPT_TIMEOUT_S = 4.0  # 요청 한 번
BUDGET_S = 6.0  # 재시도까지 합친 상한 — 화면이 이보다 오래 기다리지 않는다
SEARCH_TTL_S = 600
LOOKUP_TTL_S = 86_400
NOT_FOUND_TTL_S = 3_600  # 전자책이 아니거나 없는 번호
FAILURE_TTL_S = 60  # 알라딘이 응답하지 않을 때 — 잠깐만 기억하고 다시 시도한다
SEARCH_CACHE_SIZE = 256
LOOKUP_CACHE_SIZE = 2048

_ITEM_ID = re.compile(r"[1-9]\d{0,11}")


class StoreItem(TypedDict, total=False):
    id: str
    title: str
    author: str | None
    cover: str | None
    price: int | None
    url: str
    publisher: str | None
    pubdate: str | None
    description: str | None


_ISBN13 = re.compile(r"97[89]\d{10}")
KAKAO_URL = "https://dapi.kakao.com/v3/search/book"


def is_isbn(item_id: str) -> bool:
    return bool(_ISBN13.fullmatch(item_id or ""))


def valid_id(item_id: str) -> bool:
    """서점 책 번호 모양인지: 알라딘 ItemId(1~12자리 숫자) 또는 ISBN-13(카카오 책 검색에서 온 책)."""
    return bool(_ITEM_ID.fullmatch(item_id or "")) or is_isbn(item_id)


def product_url(item_id: str) -> str:
    return f"{PRODUCT_URL}?ItemId={item_id}&start=pebook"


def provider() -> str | None:
    """실시간으로 책을 찾아 올 곳. 카카오 책 검색 API(공식, REST API 키 필요)가 먼저고,
    없으면 BOOKSTORE_LIVE=true일 때만 알라딘 검색 화면을 읽는다.
    그 방식은 알라딘 robots.txt가 막는 경로라 기본은 꺼져 있다.
    둘 다 없으면 None: 고른 책(app/data/store.json) 안에서만 찾는다.
    (네이버 책 검색 API는 2026-07-31, 알라딘 OpenAPI는 2026-10-30에 종료됐다.)"""
    s = get_settings()
    if s.kakao_rest_api_key:
        return "kakao"
    return "aladin" if s.bookstore_live else None


def live_enabled() -> bool:
    return provider() is not None


def aladin_search_url(title: str) -> str:
    """사람이 눌러서 가는 알라딘 전자책 검색 링크(서버가 읽지 않는다)."""
    return f"{SEARCH_URL}?{urlencode({'SearchTarget': 'EBook', 'SearchWord': title})}"


def _kakao_cover(thumbnail: str) -> str | None:
    """카카오가 주는 표지는 120x174 미리보기다. 주소 안의 fname(원본)을 꺼내 큰 그림을 쓴다."""
    if not thumbnail.startswith("https://"):
        return None
    fname = parse_qs(urlsplit(thumbnail).query).get("fname", [""])[0]
    if re.fullmatch(r"https?://t1\.daumcdn\.net/lbook/image/[\w./%-]+", fname):
        return "https://" + fname.split("://", 1)[1]
    return thumbnail


def parse_kakao(data: dict) -> list[StoreItem]:
    """카카오 책 검색 응답 → 서점 책. 번호는 ISBN-13이고, 사는 곳은 알라딘 전자책 검색으로 잇는다."""
    out: list[StoreItem] = []
    seen: set[str] = set()
    for d in data.get("documents") or []:
        m = _ISBN13.search(str(d.get("isbn") or "").replace("-", ""))
        title = _clean(str(d.get("title") or ""))
        if not m or not title or m.group(0) in seen:
            continue
        seen.add(m.group(0))
        sale = d.get("sale_price")
        price = sale if isinstance(sale, int) and sale > 0 else d.get("price")
        out.append(
            StoreItem(
                id=m.group(0),
                title=title,
                author=", ".join(str(a) for a in d.get("authors") or []) or None,
                cover=_kakao_cover(str(d.get("thumbnail") or "")),
                price=price if isinstance(price, int) and price > 0 else None,
                url=aladin_search_url(title),
                publisher=_clean(str(d.get("publisher") or "")) or None,
                pubdate=str(d.get("datetime") or "")[:10] or None,
                description=_clean(str(d.get("contents") or "")) or None,
            )
        )
    return out


async def _kakao(client: httpx.AsyncClient, query: str, size: int, target: str = "") -> list[StoreItem]:
    """공식 문서: https://developers.kakao.com/docs/latest/ko/daum-search/dev-guide#search-book"""
    params: dict[str, str | int] = {"query": query, "size": size}
    if target:
        params["target"] = target
    resp = await client.get(
        KAKAO_URL,
        params=params,
        headers={"Authorization": f"KakaoAK {get_settings().kakao_rest_api_key or ''}"},
        timeout=ATTEMPT_TIMEOUT_S,
        follow_redirects=False,
    )
    if resp.status_code != 200:
        raise _Upstream(f"HTTP {resp.status_code}")
    return parse_kakao(resp.json())


# --- 파서 (scripts/build_store.py도 이 파서를 쓰면 된다) ---

_BOX = re.compile(r'<div class="ss_book_box" itemId="(\d+)">(.*?)(?=<div class="ss_book_box"|$)', re.S)
_COVER = re.compile(r'<img src="(https://image\.aladin\.co\.kr/product/[^"]+?/cover\d*/[^"]+?\.(?:jpg|png))"')
_TITLE = re.compile(r'class="bo3">([^<]+)</a>')
# "<a …AuthorSearch=…>이름</a>, <a …>이름</a> (지은이)" — 역할 하나에 딸린 이름들
_CREDIT = re.compile(
    r"((?:<a[^>]*AuthorSearch=[^>]*>[^<]+</a>(?:\s|,|&nbsp;|<span[^>]*>\s*</span>)*)+)\(([^)<]{1,12})\)",
    re.I,
)
_NAME = re.compile(r">([^<]+)</a>")
AUTHOR_ROLES = frozenset({"지은이", "글", "원작", "저자"})  # 옮긴이·그린이·감수는 지은이가 아니다
# 2026년 화면은 <em>15,750원</em>, 예전 화면은 <em>15,750</em>원
_PRICE = re.compile(r'<span class="ss_p2"><em>([\d,]+)')
_PUBLISHER = re.compile(r"PublisherSearch=[^>]*>([^<]+)</a>", re.I)
_PUBDATE = re.compile(r"\|\s*(\d{4}년(?:\s*\d{1,2}월)?)")
_META = re.compile(r'<meta\s+(?:property|name|itemprop)="([^"]+)"\s+content="([^"]*)"', re.I)
_LD_PUBLISHER = re.compile(r'"publisher"\s*:\s*\{[^}]*?"name"\s*:\s*"([^"]+)"')
_COVER_SIZE = re.compile(r"/cover\d*/")


def _clean(s: str) -> str:
    return htmllib.unescape(re.sub(r"\s+", " ", s)).strip()


def _big_cover(url: str | None) -> str | None:
    """알라딘 표지 주소만 받아 큰 판(cover500)으로 맞춘다. 다른 호스트는 버린다."""
    if not url or not url.startswith(COVER_HOST):
        return None
    return _COVER_SIZE.sub("/cover500/", url)


def _authors(fragment: str) -> str | None:
    """지은이 역할이 붙은 첫 묶음의 이름들. 옮긴이만 있으면 None."""
    for links, role in _CREDIT.findall(fragment):
        if role.strip() in AUTHOR_ROLES:
            names = [_clean(n) for n in _NAME.findall(links)]
            return ", ".join(names[:3]) or None
    return None


def _int(raw: str | None) -> int | None:
    digits = (raw or "").replace(",", "").strip()
    return int(digits) if digits.isdigit() else None


def parse_search_page(page: str) -> list[StoreItem]:
    """알라딘 전자책 검색 결과 화면 → 책 목록(화면에 나온 순서). 제목이 없는 칸은 건너뛴다."""
    items: list[StoreItem] = []
    seen: set[str] = set()
    for item_id, block in _BOX.findall(page):
        title = _TITLE.search(block)
        if not title or item_id in seen:
            continue
        seen.add(item_id)
        cover = _COVER.search(block)
        price = _PRICE.search(block)
        publisher = _PUBLISHER.search(block)
        pubdate = _PUBDATE.search(block)
        items.append(
            {
                "id": item_id,
                "title": _clean(title.group(1)),
                "author": _authors(block),
                "cover": _big_cover(cover.group(1)) if cover else None,
                "price": _int(price.group(1)) if price else None,
                "url": product_url(item_id),
                "publisher": _clean(publisher.group(1)) if publisher else None,
                "pubdate": _clean(pubdate.group(1)) if pubdate else None,
                "description": None,
            }
        )
    return items


def parse_product_page(page: str, item_id: str) -> StoreItem | None:
    """알라딘 상품 화면 → 책 한 권. 전자책이 아니거나(종이책·음반·굿즈) 제목을 못 읽으면 None."""
    meta: dict[str, str] = {}
    for key, value in _META.findall(page):
        meta.setdefault(key.lower(), htmllib.unescape(value).strip())
    if "[전자책]" not in meta.get("title", ""):  # <meta name="title" content="[전자책] 제목 : 알라딘">
        return None
    title = meta.get("og:title", "").rsplit(" | ", 1)[0].strip()  # "제목 | 지은이"
    if not title:
        return None
    return {
        "id": item_id,
        "title": title,
        "author": _authors(page) or meta.get("author") or None,
        "cover": _big_cover(meta.get("og:image")),
        "price": _int(meta.get("og:price") or meta.get("price")),
        "url": product_url(item_id),
        "publisher": (m.group(1) if (m := _LD_PUBLISHER.search(page)) else None),
        "pubdate": meta.get("datepublished") or None,
        "description": meta.get("og:description") or None,
    }


# --- 고른 책 안에서 찾기 (알라딘에 닿지 않아도 된다) ---


def _norm(query: str) -> str:
    return " ".join(query.split()).casefold()


def local_search(curated: Iterable[dict], query: str, limit: int = 20) -> list[dict]:
    """제목·지은이에 검색어가 들어 있는 고른 책. 대소문자를 가리지 않고, 띄어 쓴 낱말이 모두 있어야 한다."""
    words = _norm(query).split()
    if not words:
        return []
    out = []
    for book in curated:
        hay = f"{book.get('title') or ''} {book.get('author') or ''}".casefold()
        if all(w in hay for w in words):
            out.append(book)
            if len(out) >= limit:
                break
    return out


def merge(local: list[dict], live: list[dict], curated_index: dict[str, dict], limit: int = 20) -> list[dict]:
    """고른 책을 앞에 둔다: 고른 책에서 찾은 것 → 알라딘 결과 가운데 고른 책 → 나머지. id가 같으면 한 번만."""
    out: list[dict] = []
    seen: set[str] = set()

    def add(book: dict) -> None:
        if book["id"] not in seen:
            seen.add(book["id"])
            out.append(book)

    for book in local:
        add(book)
    for book in live:
        if book["id"] in curated_index:
            add(curated_index[book["id"]])
    for book in live:
        add(book)
    return out[:limit]


# --- 메모리 캐시 ---


def _clock() -> float:  # 테스트에서 바꿔 시간을 넘긴다
    return time.monotonic()


class _TTLCache:
    """개수 상한이 있는 TTL 캐시. 가득 차면 가장 오래 안 쓴 것부터 버린다."""

    def __init__(self, maxsize: int) -> None:
        self.maxsize = maxsize
        self._data: OrderedDict[str, tuple[float, Any]] = OrderedDict()

    def get(self, key: str) -> tuple[bool, Any]:
        entry = self._data.get(key)
        if entry is None:
            return False, None
        if entry[0] <= _clock():
            del self._data[key]
            return False, None
        self._data.move_to_end(key)
        return True, entry[1]

    def put(self, key: str, value: Any, ttl: float) -> None:
        self._data[key] = (_clock() + ttl, value)
        self._data.move_to_end(key)
        while len(self._data) > self.maxsize:
            self._data.popitem(last=False)

    def clear(self) -> None:
        self._data.clear()

    def __len__(self) -> int:
        return len(self._data)


_search_cache = _TTLCache(SEARCH_CACHE_SIZE)
_lookup_cache = _TTLCache(LOOKUP_CACHE_SIZE)


def clear_cache() -> None:
    _search_cache.clear()
    _lookup_cache.clear()


def cached_search(query: str) -> list[StoreItem] | None:
    """캐시에 있으면 그 결과, 없으면 None — 부르는 쪽이 새로 가져올지(속도 제한) 정한다."""
    hit, value = _search_cache.get(_norm(query))
    return value if hit else None


def cached_lookup(item_id: str) -> tuple[bool, StoreItem | None]:
    """(캐시에 있는지, 값). 값 None은 '없는 책'으로 기억해 둔 것이다."""
    return _lookup_cache.get(item_id)


# --- 실시간 조회 ---


class _Upstream(Exception):
    """알라딘이 200이 아닌 응답을 줬다."""


def _reason(e: Exception) -> str:
    """로그에 남길 실패 이유 — 예외 종류와, 알라딘 응답이면 상태 코드만."""
    return f"{type(e).__name__} {e}" if isinstance(e, _Upstream) else type(e).__name__


async def _fetch(client: httpx.AsyncClient, url: str, params: dict[str, str]) -> str:
    """정해진 알라딘 주소 하나를 읽는다. 연결 오류·5xx는 한 번만 다시 하고, 모두 합쳐 BUDGET_S 안에 끝낸다."""
    async with asyncio.timeout(BUDGET_S):
        for attempt in (0, 1):
            try:
                resp = await client.get(url, params=params, timeout=ATTEMPT_TIMEOUT_S, follow_redirects=False)
            except httpx.TransportError:
                if attempt:
                    raise
                continue
            if resp.status_code >= 500 and not attempt:
                continue
            if resp.status_code != 200:
                raise _Upstream(f"HTTP {resp.status_code}")
            return resp.text
    raise _Upstream("unreachable")  # pragma: no cover


async def search(client: httpx.AsyncClient, query: str, limit: int = 20) -> list[StoreItem]:
    """알라딘 전자책 검색. 실패하거나 꺼져 있으면 []."""
    key = _norm(query)
    if not key:
        return []
    hit, cached = _search_cache.get(key)
    if hit:
        return cached[:limit]
    if not live_enabled():
        return []
    try:
        if provider() == "kakao":
            items = await _kakao(client, " ".join(query.split()), 20)
        else:
            params = {"SearchTarget": "eBook", "SearchWord": " ".join(query.split())}
            page = await _fetch(client, SEARCH_URL, params)
            items = parse_search_page(page)
    except Exception as e:  # 화면을 깨뜨리지 않는다. 검색어는 로그에 남기지 않는다
        log.warning("bookstore search failed: %s", _reason(e))
        _search_cache.put(key, [], FAILURE_TTL_S)
        return []
    _search_cache.put(key, items, SEARCH_TTL_S)
    for item in items:
        # 지은이까지 읽힌 책은 상품 화면을 다시 읽지 않아도 된다
        if item.get("author") and item.get("cover") and not _lookup_cache.get(item["id"])[0]:
            _lookup_cache.put(item["id"], item, LOOKUP_TTL_S)
    return items[:limit]


async def lookup(client: httpx.AsyncClient, item_id: str) -> StoreItem | None:
    """알라딘 ItemId 한 권. 전자책이 아니거나 실패하면 None."""
    if not valid_id(item_id):
        return None
    hit, cached = _lookup_cache.get(item_id)
    if hit:
        return cached
    if not live_enabled():
        return None
    try:
        if is_isbn(item_id):
            # ISBN은 카카오 책 검색에서 온 번호다 — 키가 없으면 찾을 곳이 없다
            found = await _kakao(client, item_id, 5, target="isbn") if provider() == "kakao" else []
            item = next((i for i in found if i["id"] == item_id), None)
        elif get_settings().bookstore_live:
            page = await _fetch(client, PRODUCT_URL, {"ItemId": item_id})
            item = parse_product_page(page, item_id)
        else:
            return None
    except Exception as e:
        log.warning("bookstore lookup failed: %s", _reason(e))
        _lookup_cache.put(item_id, None, FAILURE_TTL_S)
        return None
    _lookup_cache.put(item_id, item, LOOKUP_TTL_S if item else NOT_FOUND_TTL_S)
    return item
