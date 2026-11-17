"""app/data 의 서가(catalog.json)·서점(store.json) 데이터가 깨지지 않았는지 살핀다."""

import collections
import json
import re

from app import web

CATALOG = json.loads((web.DATA / "catalog.json").read_text())
STORE = json.loads((web.DATA / "store.json").read_text())
BY_KEY = {(b["source"], b["id"]): b for b in CATALOG}

# 가장 유명한 세계 고전 20권 (구텐베르크 번호) — 「작은 아씨들」은 두 판 가운데 하나만 있어도 된다
FAMOUS = {
    "1342": "오만과 편견",
    "84": "프랑켄슈타인",
    "11": "이상한 나라의 앨리스",
    "1661": "셜록 홈즈의 모험",
    "345": "드라큘라",
    "2701": "모비 딕",
    "64317": "위대한 개츠비",
    "2554": "죄와 벌",
    "5200": "변신",
    "2500": "싯다르타",
    "174": "도리언 그레이의 초상",
    "1260": "제인 에어",
    "768": "폭풍의 언덕",
    "135": "레 미제라블",
    "2600": "전쟁과 평화",
    "1727": "오디세이아",
    "1513": "로미오와 줄리엣",
    "1524": "햄릿",
    "514": "작은 아씨들",
    "120": "보물섬",
}
# 본문이 하위 문서에 나뉘어 있어 예전에는 열리지 않던 한국 작품들
MULTI_PAGE = ["상록수", "무정", "탁류", "태평천하", "흙", "님의 침묵", "진달래꽃 (시집)", "하늘과 바람과 별과 시 (1955년)"]


def test_catalog_keys_are_unique_and_fields_are_complete():
    assert len(BY_KEY) == len(CATALOG)
    for b in CATALOG:
        assert {"source", "id", "title", "author", "year", "genre", "lang"} <= set(b), b
        assert b["source"] in {"wikisource", "gutendex"}
        assert isinstance(b["id"], str) and b["id"].strip() == b["id"] and b["id"]
        assert isinstance(b["title"], str) and b["title"].strip()
        assert b["author"] is None or (isinstance(b["author"], str) and b["author"].strip())
        assert b["year"] is None or isinstance(b["year"], int)
        assert b["genre"] in web.GENRE_ORDER, b  # 탐색 화면의 서가에 꽂히는 장르만
        if b["source"] == "gutendex":
            assert b["id"].isdigit()
        else:
            assert b["lang"] == "ko" and "/" not in b["id"]  # 하위 문서는 책 한 권이 아니다
        if b.get("cover"):
            assert b["cover"].startswith("/static/covers/") and b.get("cover_src", "").startswith("https://")


def test_catalog_has_grown_on_both_sides():
    count = collections.Counter(b["source"] for b in CATALOG)
    assert count["wikisource"] >= 391 + 150  # 한국 퍼블릭 도메인 작품 +150 이상
    # 세계 고전: 읽을 글이 없는 47권(오디오북·PDF 전용 등)을 빼고 새 책을 더했다.
    # 900권까지 늘리려던 보충은 구텐베르크 점검(503)으로 끝내지 못했다
    assert count["gutendex"] >= 450
    assert len(web.CATALOG) >= 1300  # 같은 제목·저자를 합친 뒤 앱이 실제로 보여 주는 수


def test_most_famous_world_classics_are_present_with_korean_titles_first():
    for gid, korean in FAMOUS.items():
        book = BY_KEY[("gutendex", gid)]
        assert book["title"].startswith(korean), book["title"]
        assert re.search(r"\([A-Z][^)]+\)$", book["title"]), book["title"]  # 원제는 괄호 안에 남는다(영어 검색)
    assert ("gutendex", "1342") in web.CATALOG_INDEX  # 인기 순위·홈 나선이 쓰는 색인에도 그대로 있다


def test_korean_multi_page_works_are_in_the_catalog():
    for title in MULTI_PAGE:
        book = BY_KEY[("wikisource", title)]
        assert book["author"] and book["genre"] in {"장편소설", "시집"}
    # 판본 목록 문서가 아니라 실제 판본을 가리킨다
    assert ("wikisource", "하늘과 바람과 별과 시") not in BY_KEY
    assert BY_KEY[("wikisource", "하늘과 바람과 별과 시 (1955년)")]["title"] == "하늘과 바람과 별과 시"
    # 같은 시집의 초판은 제목이 겹치지 않게 판을 밝혀 적는다(앱은 제목·저자가 같은 항목을 하나로 합친다)
    assert BY_KEY[("wikisource", "하늘과 바람과 별과 시 (1948년)")]["title"] == "하늘과 바람과 별과 시 (1948년)"


def test_popular_order_and_showcase_point_at_catalog_books():
    assert all(key in web.CATALOG_INDEX for key in web.POPULAR_ORDER)
    readable = [b for b in web.SHOWCASE if b.get("source")]
    assert readable and all(b["readable"] for b in readable)


def test_store_is_bigger_and_well_formed():
    assert len(STORE) >= 250
    assert len({b["id"] for b in STORE}) == len(STORE)
    assert len({(b["title"], b["author"]) for b in STORE}) == len(STORE)
    for b in STORE:
        assert set(b) == {"id", "title", "author", "cover", "price", "url"}
        assert b["id"].isdigit() and b["title"].strip()
        assert b["url"] == f"https://www.aladin.co.kr/shop/wproduct.aspx?ItemId={b['id']}&start=pebook"
        assert b["cover"].startswith("https://image.aladin.co.kr/product/") and "/cover500/" in b["cover"]
    for b in STORE[130:]:  # 새로 넣은 책은 지은이까지 확인했다
        assert b["author"]
    titles = [b["title"] for b in STORE]
    for famous in ("사피엔스", "데일 카네기 인간관계론", "폭풍의 언덕", "그리스인 조르바", "돈의 심리학", "월든"):
        assert any(t.startswith(famous) for t in titles), famous
