"""지금 많이 팔리는 책 — 홈 나선 맨 앞에 세 권을 건다.

Yes24 종합 베스트 화면(/product/category/bestseller)을 하루 한 번 읽어 app/data/bestsellers.json에 적는다
(scripts/build_bestsellers.py, .github/workflows/bestsellers.yml). 이 경로는 Yes24 robots.txt가 막지 않는다.
앱은 그 파일만 읽는다 — 요청을 처리하는 동안에는 Yes24에 접속하지 않는다.
"""

import html as htmllib
import json
import re
from pathlib import Path

from app.services import bookstore

DATA = Path(__file__).resolve().parent.parent / "data" / "bestsellers.json"
URL = "https://www.yes24.com/product/category/bestseller"
PARAMS = {"categoryNumber": "001", "pageNumber": "1", "pageSize": "24"}  # 001 = 국내도서 종합
ID_PREFIX = "y"  # 서점 책 번호(알라딘 ItemId·ISBN)와 겹치지 않게 Yes24 상품 번호 앞에 붙인다

_ITEM = re.compile(r'(?=<li[^>]*data-goods-no=")')
_GOODS = re.compile(r'data-goods-no="(\d{1,12})"')
_NAME = re.compile(r'class="gd_name"[^>]*>([^<]+)<')
_AUTH = re.compile(r'class="authPub info_auth"[^>]*>(.*?)</span>', re.S)
_PUB = re.compile(r'class="authPub info_pub"[^>]*>(.*?)</span>', re.S)
_PRICE = re.compile(r'class="yes_b">([\d,]+)<')
_ROLE = re.compile(r"\s*(원저|공저|편저|글그림|지음|저|글|편)$")


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", "", fragment))).strip()


def _author(raw: str) -> str | None:
    """'무라카미 하루키 저/홍은주 역' → '무라카미 하루키'. 옮긴이·그린이는 뺀다."""
    first = raw.split("/")[0].strip()
    return _ROLE.sub("", first).strip() or None


def parse_yes24(page: str, limit: int = 10) -> list[dict]:
    """베스트 화면 → 순위대로 [{id, title, author, publisher, price, cover, url, rank}]."""
    out: list[dict] = []
    seen: set[str] = set()
    for block in _ITEM.split(page)[1:]:
        goods, name = _GOODS.search(block), _NAME.search(block)
        if not goods or not name or goods.group(1) in seen:
            continue
        seen.add(goods.group(1))
        title = _text(name.group(1))
        auth, pub, price = _AUTH.search(block), _PUB.search(block), _PRICE.search(block)
        out.append(
            {
                "id": f"{ID_PREFIX}{goods.group(1)}",
                "title": title,
                "author": _author(_text(auth.group(1))) if auth else None,
                "publisher": _text(pub.group(1)) if pub else None,
                "price": int(price.group(1).replace(",", "")) if price else None,
                "cover": f"https://image.yes24.com/goods/{goods.group(1)}/XL",
                "url": bookstore.aladin_search_url(title),
                "rank": len(out) + 1,
            }
        )
        if len(out) >= limit:
            break
    return out


def load() -> list[dict]:
    """적어 둔 베스트셀러. 파일이 없거나 깨졌으면 빈 목록(홈은 고른 책만으로 선다)."""
    try:
        books = json.loads(DATA.read_text(encoding="utf-8"))["books"]
    except (OSError, ValueError, KeyError, TypeError):
        return []
    ok = [b for b in books if isinstance(b, dict) and b.get("id") and b.get("title") and b.get("cover")]
    return sorted(ok, key=lambda b: b.get("rank") or 99)
