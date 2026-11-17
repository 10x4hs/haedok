"""서가 카탈로그(app/data/catalog.json)를 만든다.

- 위키문헌: 장르 분류에 속하거나 퍼블릭 도메인 작가의 저자 문서에 실린 글 가운데, 저자 사망 연도로
  퍼블릭 도메인이 확인되는 문서만.
  한국은 2013년 보호기간이 사후 50→70년으로 늘었지만 1962년 이전 사망 저자의 작품은
  이미 만료되어 계속 퍼블릭 도메인이다. 그래서 `PD-old-70`은 그대로, `PD-old-50`은
  저자 문서의 `NNNN년 죽음` 분류로 1962년 이전 사망이 확인될 때만 넣는다.
  옮긴 글은 옮긴이도 1962년 이전에 사망한 것이 확인될 때만 싣고, 번역 저작권을 알 수 없는 문서는 뺀다.
  저자 문서로 이어지지 않는 글은 조선 시대 고전소설·판소리가 아니면 새로 싣지 않는다.
  장편·시집처럼 본문이 하위 문서에 나뉜 작품(차례 문서)은 앱의 위키문헌 어댑터가 한 권으로 이어 준다.
  새로 넣는 글은 그 어댑터로 실제 본문을 받아 보고 300자 이상일 때만 싣는다. 번역 문서는 뺀다.
- 구텐베르크: 서가(추리·SF·동화 …)마다, 그리고 전체에서 인기(내려받기) 순서로 영어·한국어 평문이 있는 책.
  갓 올라와 잠깐 순위에 든 책, 사전·연감 같은 읽을거리가 아닌 책, 성인물은 뺀다.

이미 실린 항목은 그대로 두고(표지·주소가 바뀌지 않는다) 새 항목만 뒤에 보탠다.
실행: python scripts/build_catalog.py [--no-wiki] [--no-gutenberg]
"""

import asyncio
import json
import re
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.sources.base import SourceError  # noqa: E402
from app.sources.wikisource import WikisourceSource  # noqa: E402

WIKI_API = "https://ko.wikisource.org/w/api.php"
GUTENDEX = "https://gutendex.com/books/"
OUT = ROOT / "app" / "data" / "catalog.json"
UA = {"User-Agent": "haedok-catalog/0.1 (+https://github.com/10x4hs/haedok)"}

GENRES = [
    "단편소설",
    "중편소설",
    "장편소설",
    "고전소설",
    "신소설",
    "동화",
    "시",
    "시집",
    "희곡",
    "판소리",
    "수필",
]
# 저자 문서에서 글을 더 모으는 작가 → 장르 분류가 없는 글을 꽂을 서가.
# 여기 이름이 있다고 싣는 것이 아니다: 문서마다 아래의 퍼블릭 도메인 확인을 똑같이 거친다.
AUTHORS = {
    "김유정": "단편소설",
    "이상": "단편소설",
    "현진건": "단편소설",
    "이효석": "단편소설",
    "김동인": "단편소설",
    "나도향": "단편소설",
    "채만식": "단편소설",
    "최서해": "단편소설",
    "강경애": "단편소설",
    "계용묵": "단편소설",
    "이광수": "단편소설",
    "심훈": "단편소설",
    "백신애": "단편소설",
    "김명순": "단편소설",
    "나혜석": "단편소설",
    "이익상": "단편소설",
    "조명희": "단편소설",
    "김남천": "단편소설",
    "지하련": "단편소설",
    "윤동주": "시",
    "김소월": "시",
    "한용운": "시",
    "이육사": "시",
    "김영랑": "시",
    "이상화": "시",
    "정지용": "시",
    "박용철": "시",
    "이장희": "시",
    "홍사용": "시",
    "노천명": "시",
    "오장환": "시",
    "임화": "시",
    "변영로": "시",
    "윤곤강": "시",
    "방정환": "동화",
    "최남선": "수필",
    "신채호": "수필",
    "김구": "수필",
    "안국선": "수필",
    "이인직": "신소설",
    "이해조": "신소설",
    "최찬식": "신소설",
    "김우진": "희곡",
    "함세덕": "희곡",
}
# 분류 수집에서 빠지기 쉬운 유명작(차례 문서 등) → 서가
EXTRA = {
    "상록수": "장편소설",
    "무정": "장편소설",
    "탁류": "장편소설",
    "태평천하": "장편소설",
    "흙": "장편소설",
    "님의 침묵": "시집",
    "진달래꽃 (시집)": "시집",
    "하늘과 바람과 별과 시 (1948년)": "시집",
    "심청전": "고전소설",
    "조웅전": "고전소설",
    "인현왕후전": "고전소설",
    "운영전": "고전소설",
    "한중록": "수필",
    "혈의 누": "신소설",
    "금수회의록": "신소설",
    "자유종": "신소설",
    "은세계": "신소설",
    "추월색": "신소설",
}
# 여러 판본을 모아 둔 문서(판본 문서): 그 문서가 가리키는 판본들을 살핀다 → 서가
VERSION_PAGES = {
    "흥부전": "고전소설",
    "토끼전": "고전소설",
    "장화홍련전": "고전소설",
    "숙향전": "고전소설",
    "숙영낭자전": "고전소설",
    "유충렬전": "고전소설",
    "이춘풍전": "고전소설",
    "배비장전": "고전소설",
    "전우치전": "고전소설",
    "홍길동전": "고전소설",
    "춘향전": "고전소설",
}
# 서가 이름과 다른 분류 → 꽂을 서가 (퍼블릭 도메인 표시는 따로 있어야 한다)
MORE_CATEGORIES = {"판소리 소설": "고전소설"}
# 싣지 않는 문서: 퍼블릭 도메인 표시는 있지만 뒤에 붙은 글(후기·해설)의 저작권을 확인하지 못한 판본.
# 같은 시집의 1948년 초판과 1955년 증보판(윤동주의 글만 옮겨져 있다)은 실려 있다
EXCLUDE = {"하늘과 바람과 별과 시 (1979년)"}
# 구텐베르크 주제·서가에 이 말이 있으면 → 서가 (위에서부터 먼저 맞는 것, 없으면 세계 고전)
WORLD = [
    (("detective", "mystery", "crime, thrillers"), "세계 · 추리"),
    (("science fiction",), "세계 · SF"),
    (("fantasy",), "세계 · 판타지"),
    (("fairy tales",), "세계 · 동화"),
    (("horror", "gothic fiction", "ghost stories"), "세계 · 공포"),
    (("adventure stories", "category: adventure"), "세계 · 모험"),
    (("category: poetry", "poetry"), "세계 · 시"),
    (("category: plays", "drama"), "세계 · 희곡"),
    (("category: philosophy", "philosophy"), "세계 · 철학"),
    (("category: children", "children's stories", "juvenile fiction"), "세계 · 어린이"),
    (("category: history",), "세계 · 역사"),
    (("love stories", "category: romance"), "세계 · 로맨스"),
]
# 읽을거리가 아닌 것(사전·색인 등)
_NOT_READING = re.compile(
    r"dictionary|encyclop|thesaurus|index of|magazine|periodical|factbook|almanac|handbook|catalog"
    r"|\bvol(?:ume)?\.? \d+ of\b",
    re.I,
)
_ADULT = re.compile(r"erotic|pornograph", re.I)  # 주제·서가에 이 말이 있는 책은 싣지 않는다
# 서가별로 인기 순서를 묻는 gutendex 주제어 → 서가. 한 서가에서 새로 받는 책 수는 SHELF_QUOTA까지
TOPICS = {
    "detective": "세계 · 추리",
    "science fiction": "세계 · SF",
    "fantasy": "세계 · 판타지",
    "fairy tales": "세계 · 동화",
    "adventure": "세계 · 모험",
    "horror": "세계 · 공포",
    "love stories": "세계 · 로맨스",
    "poetry": "세계 · 시",
    "drama": "세계 · 희곡",
    "philosophy": "세계 · 철학",
    "children": "세계 · 어린이",
    "history": "세계 · 역사",
}
SHELF_QUOTA = 24
DEFAULT_WORLD = "세계 고전"
GUTENBERG_TARGET = 900  # 구텐베르크 책을 이만큼까지 인기 순으로 채운다
GUTENBERG_LANGS = {"en", "ko"}
# gutendex의 인기 순위는 최근 30일 내려받기 수라, 갓 올라온 책이 잠깐 상위에 뜬다(유명해서가 아니다).
# 가장 새 번호에서 이만큼 안쪽의 책은 건너뛴다 — 구텐베르크는 한 달에 300권쯤 새로 싣는다.
GUTENBERG_RECENT = 3000
# 가장 유명한 고전은 한국어 제목을 앞세운다: 「오만과 편견 (Pride and Prejudice)」
KO_TITLES = {
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
    "37106": "작은 아씨들",
    "120": "보물섬",
}
MIN_BODY_CHARS = 800
MIN_POEM_CHARS = 120  # 시는 짧다
MIN_READ_CHARS = 300  # 새로 싣는 글이 앱에서 실제로 열렸을 때의 최소 본문 길이
# 조선 시대 고전소설·판소리는 작자 미상의 수백 년 된 작품이라 그 자체로 퍼블릭 도메인
ANCIENT_GENRES = {"고전소설", "판소리"}
PD_TAGS = {"분류:PD-old-70", "분류:PD-old-100", "분류:PD-old"}
LAST_PD_DEATH_YEAR = 1962

_YEAR = re.compile(r"분류:(\d{4})년 작품")
_DEATH = re.compile(r"분류:(\d{4})년 죽음")
# 머리말 틀의 저자 칸: |저자 = [[저자:김유정|김유정]] · |지은이 = … · |글쓴이 = [[글쓴이:김만중|김만중]]
_AUTHOR_PAGE = re.compile(r"\|\s*(?:저자|지은이|글쓴이)\s*=\s*\[\[\s*(?:저자|글쓴이):([^|\]]+)")
# 옮긴이 칸: |역자 = [[저자:최남선|최남선]] (비어 있으면 옮긴 글이 아니다)
_TRANSLATOR = re.compile(r"\|\s*(?:역자|옮긴이|번역자)\s*=[ \t]*([^\n|}]*)")
_PERSON_LINK = re.compile(r"\[\[\s*(?:저자|글쓴이):([^|\]]+)")
NO_TRANSLATION_RIGHTS = "분류:번역 저작권 정보 없음"  # 원작만 퍼블릭 도메인이고 번역은 알 수 없는 문서
_TEMPLATE = re.compile(r"\{\{.*?\}\}", re.S)
_LINK_LINE = re.compile(r"^\s*[*#:]*\s*\[\[[^\]]*\]\]\s*$", re.M)
# 본문이 다른 곳에 있는 문서: 하위 문서 링크([[/제1장]], [[제목/제1장]])나 색인 끼워 넣기(<pages …/>)
_SUBPAGE_LINK = re.compile(r"\[\[\s*/[^\]|]+")
_PAGES_TAG = re.compile(r"<pages\s", re.I)
_PAREN = re.compile(r"\s*\([^)]*\)$")


async def wiki(client: httpx.AsyncClient, **params) -> dict:
    """위키미디어 API — 너무 빨리 부르면 빈 응답이 오므로 쉬었다가 다시 시도한다."""
    params |= {"action": "query", "format": "json", "formatversion": 2}
    for attempt in range(5):
        r = await client.get(WIKI_API, params=params)
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("application/json"):
            return r.json()
        await asyncio.sleep(2 + attempt * 3)
    r.raise_for_status()
    return r.json()


async def members(client: httpx.AsyncClient, genre: str) -> list[str]:
    titles, cont = [], {}
    while True:
        d = await wiki(
            client, list="categorymembers", cmtitle=f"분류:{genre}", cmnamespace=0, cmlimit=500, **cont
        )
        titles += [m["title"] for m in d["query"]["categorymembers"]]
        if "continue" not in d:
            return titles
        cont = {"cmcontinue": d["continue"]["cmcontinue"]}


async def linked(client: httpx.AsyncClient, page: str) -> list[str]:
    """문서(저자 문서·판본 문서)가 가리키는 본문 문서들."""
    titles, cont = [], {}
    while True:
        d = await wiki(client, titles=page, prop="links", plnamespace=0, pllimit="max", redirects=1, **cont)
        for found in d["query"]["pages"]:
            titles += [link["title"] for link in found.get("links", [])]
        if "continue" not in d:
            return titles
        cont = {"plcontinue": d["continue"]["plcontinue"]}


def body_chars(wikitext: str) -> int:
    text = _LINK_LINE.sub("", _TEMPLATE.sub("", wikitext))
    return len(re.sub(r"\s+", "", text))


def is_index(wikitext: str, title: str) -> bool:
    """본문이 하위 문서나 색인에 있는 문서인지."""
    return bool(_SUBPAGE_LINK.search(wikitext) or _PAGES_TAG.search(wikitext) or f"[[{title}/" in wikitext)


def is_public_domain(cats: list[str], genre_cat: str | None, died: int | None) -> bool:
    """퍼블릭 도메인 확인: PD-old-70 계열 표시, 또는 PD-old-50이면서 저자가 1962년 이전에 사망.

    저자의 사망 연도를 아는데 1962년보다 뒤라면 표시가 무엇이든 싣지 않는다.
    """
    if died is not None and died > LAST_PD_DEATH_YEAR:
        return False
    if genre_cat in ANCIENT_GENRES or PD_TAGS & set(cats):
        return True
    return "분류:PD-old-50" in cats and died is not None


def author_of(wikitext: str) -> str | None:
    """머리말 틀에 저자 문서로 이어진 이름. 「미상」처럼 문서가 없는 이름은 None."""
    m = _AUTHOR_PAGE.search(wikitext)
    return m.group(1).strip() if m else None


def translator_of(wikitext: str) -> tuple[bool, str | None]:
    """(옮긴 글인지, 저자 문서로 이어진 옮긴이 이름). 옮긴이가 적혀 있지만 문서가 없으면 (True, None)."""
    m = _TRANSLATOR.search(wikitext)
    value = m.group(1).strip() if m else ""
    if not value:
        return False, None
    link = _PERSON_LINK.search(value)
    return True, link.group(1).strip() if link else None


_deaths: dict[str, int | None] = {}


async def death_year(client: httpx.AsyncClient, author: str) -> int | None:
    if author not in _deaths:
        d = await wiki(client, titles=f"저자:{author}", prop="categories", cllimit="max", redirects=1)
        cats = [c["title"] for p in d["query"]["pages"] for c in p.get("categories", [])]
        _deaths[author] = next((int(m.group(1)) for c in cats if (m := _DEATH.search(c))), None)
    return _deaths[author]


def guess_genre(cats: list[str], wikitext: str, fallback: str) -> tuple[str, str | None]:
    """(꽂을 서가, 문서에 실제로 달린 장르 분류)."""
    for genre in GENRES:
        if f"분류:{genre}" in cats:
            return genre, genre
    for category, shelf in MORE_CATEGORIES.items():
        if f"분류:{category}" in cats:
            return shelf, None
    if "<poem" in wikitext.lower() and body_chars(wikitext) < 4000:
        return "시", None
    return fallback, None


async def describe(
    client: httpx.AsyncClient, titles: list[str], genre: str, author: str | None = None
) -> list[dict]:
    """문서들의 분류·원문을 읽어 실을 수 있는 것만 카탈로그 항목으로 만든다.

    genre는 분류로 모을 때는 그 분류, 그 밖에는(저자 문서·유명작 목록) 장르 분류가 없는 글을 꽂을 서가다.
    author를 주면 머리말에 그 저자가 적힌 글만 받는다.
    """
    out = []
    for i in range(0, len(titles), 25):
        d = await wiki(
            client,
            titles="|".join(titles[i : i + 25]),
            prop="categories|revisions",
            cllimit="max",
            rvprop="content",
            rvslots="main",
        )
        for p in d["query"]["pages"]:
            cats = [c["title"] for c in p.get("categories", [])]
            if "revisions" not in p:
                continue
            src = p["revisions"][0]["slots"]["main"]["content"]
            name = author_of(src)
            if author and name != author:
                continue  # 저자 문서가 가리키기만 하는 남의 글
            by_category = author is None and f"분류:{genre}" in cats
            shelf, tagged = (genre, genre) if by_category else guess_genre(cats, src, genre)
            long_enough = body_chars(src) >= (MIN_POEM_CHARS if shelf in ("시", "시집") else MIN_BODY_CHARS)
            if not shelf or (not long_enough and not is_index(src, p["title"])):
                continue
            died = await death_year(client, name) if name else None
            if not is_public_domain(cats, tagged, died):
                continue
            if name is None and shelf not in ANCIENT_GENRES:
                continue  # 저자를 확인할 수 없는 근대 글·옮긴 글(「맥베스」 등)
            translated, translator = translator_of(src)
            if NO_TRANSLATION_RIGHTS in cats or (translated and not translator):
                continue
            if translator and not is_public_domain(
                ["분류:PD-old-50"], None, await death_year(client, translator)
            ):
                continue  # 옮긴이의 권리가 남아 있거나 사망 연도를 모른다
            year = next((int(m.group(1)) for c in cats if (m := _YEAR.search(c))), None)
            out.append(
                {
                    "source": "wikisource",
                    "id": p["title"],
                    "title": _PAREN.sub("", p["title"]),
                    "author": name,
                    "year": year,
                    "genre": shelf,
                    "lang": "ko",
                }
            )
        await asyncio.sleep(1.0)  # 위키미디어 서버 배려
    return out


async def backfill_authors(client: httpx.AsyncClient, books: list[dict]) -> int:
    """저자가 비어 있는 위키문헌 항목에, 머리말 틀에 적힌 저자를 채운다(지은이·글쓴이로 적힌 문서들)."""
    blank = {b["id"]: b for b in books if b["source"] == "wikisource" and not b.get("author")}
    titles, filled = list(blank), 0
    for i in range(0, len(titles), 25):
        d = await wiki(
            client, titles="|".join(titles[i : i + 25]), prop="revisions", rvprop="content", rvslots="main"
        )
        for p in d["query"]["pages"]:
            if "revisions" in p and (name := author_of(p["revisions"][0]["slots"]["main"]["content"])):
                blank[p["title"]]["author"] = name
                filled += 1
        await asyncio.sleep(1.0)
    return filled


async def readable_chars(source: WikisourceSource, title: str) -> int:
    """앱이 이 문서를 열었을 때 나오는 본문 길이(공백 제외). 본문이 없으면 0."""
    for attempt in range(4):
        try:
            _, text = await source.fetch_book(title)
            return len(re.sub(r"\s+", "", text))
        except SourceError:
            return 0
        except (httpx.HTTPError, ValueError):
            await asyncio.sleep(3 + attempt * 4)
    return 0


async def wikisource_new(client: httpx.AsyncClient, have: set[str]) -> list[dict]:
    """아직 없는 위키문헌 글을 모으고, 실제로 열어 보아 읽을 수 있는 것만 돌려준다."""
    found: dict[str, dict] = {}

    def keep(books: list[dict]) -> int:
        fresh = [b for b in books if b["id"] not in found]
        found.update({b["id"]: b for b in fresh})
        return len(fresh)

    def wanted(titles: list[str]) -> list[str]:
        skip = have | found.keys() | EXCLUDE
        return [t for t in titles if t not in skip and "/" not in t and "번역:" not in t]

    for title, shelf in EXTRA.items():
        keep([b | {"genre": shelf} for b in await describe(client, wanted([title]), shelf)])
    print(f"유명작: {len(found)}")
    for page, shelf in VERSION_PAGES.items():
        got = await describe(client, wanted(await linked(client, page)), shelf)
        print(f"{page} 판본: {keep([b | {'genre': shelf} for b in got])}")
    for genre in GENRES:
        print(f"{genre}: {keep(await describe(client, wanted(await members(client, genre)), genre))}")
    for category, shelf in MORE_CATEGORIES.items():
        print(f"{category}: {keep(await describe(client, wanted(await members(client, category)), shelf))}")
    for author, shelf in AUTHORS.items():
        titles = wanted(await linked(client, f"저자:{author}"))
        print(f"{author}: {keep(await describe(client, titles, shelf, author))}")

    source, ok = WikisourceSource(client), []
    for n, book in enumerate(found.values(), 1):
        size = await readable_chars(source, book["id"])
        if size >= MIN_READ_CHARS:
            ok.append(book)
        else:
            print(f"  본문 부족({size}자): {book['id']}")
        if n % 50 == 0:
            print(f"  확인 {n}/{len(found)} — 읽을 수 있는 글 {len(ok)}")
        await asyncio.sleep(0.6)
    return ok


def world_shelf(book: dict) -> str:
    topics = " ".join(book.get("subjects", []) + book.get("bookshelves", [])).lower()
    return next((shelf for words, shelf in WORLD if any(w in topics for w in words)), DEFAULT_WORLD)


def _name(gutendex_author: str) -> str:
    """'Austen, Jane' → 'Jane Austen'."""
    last, _, first = gutendex_author.partition(", ")
    return f"{first} {last}".strip()


def gutenberg_entry(book: dict) -> dict | None:
    """gutendex 책 정보 → 카탈로그 항목. 글이 아니거나 평문이 없거나 다른 언어면 None."""
    formats = book.get("formats") or {}
    has_text = any(k.startswith("text/plain") and not v.endswith(".zip") for k, v in formats.items())
    languages = set(book.get("languages", []))
    if book.get("media_type") != "Text" or not has_text or not GUTENBERG_LANGS & languages:
        return None
    topics = " ".join(book.get("subjects", []) + book.get("bookshelves", []))
    if _NOT_READING.search(book.get("title", "")) or _ADULT.search(topics):
        return None
    authors = [_name(a["name"]) for a in book.get("authors", [])]
    return {
        "source": "gutendex",
        "id": str(book["id"]),
        "title": " ".join(book["title"].replace(" : $b ", ": ").split()),  # $b = 부제 표시(MARC)
        "author": authors[0] if authors else None,
        "year": None,
        "genre": world_shelf(book),
        "lang": "ko" if "ko" in book["languages"] else None,
    }


async def gutendex_pages(
    client: httpx.AsyncClient, language: str, topic: str = "", tries: int = 3, timeout: float = 60
):
    """gutendex 목록을 내려받기 많은 순서(기본 정렬)로 한 쪽씩 내놓는다. 응답이 없으면 거기서 멈춘다."""
    url: str | None = str(
        httpx.URL(GUTENDEX, params={"languages": language} | ({"topic": topic} if topic else {}))
    )
    while url:
        data = None
        for attempt in range(tries):
            try:
                r = await client.get(url, timeout=timeout)
                r.raise_for_status()
                data = r.json()
                break
            except (httpx.HTTPError, ValueError):
                await asyncio.sleep(5 + attempt * 10)
        if data is None:
            print(f"  gutendex({language} {topic})가 응답하지 않아 여기서 멈춘다")
            return
        yield data.get("results", [])
        url = data.get("next")
        await asyncio.sleep(1.0)  # 서버 배려


def not_recent(entries: list[dict], newest: int) -> list[dict]:
    """갓 올라와 잠깐 순위에 든 책을 뺀다."""
    return [e for e in entries if int(e["id"]) <= newest - GUTENBERG_RECENT]


async def gutenberg_new(client: httpx.AsyncClient, have: set[str], need: int, progress=None) -> list[dict]:
    """아직 없는 구텐베르크 책을 need권까지: 한국어 책(있으면) → 서가마다 인기 순 → 전체 인기 순.

    gutendex는 자주 느려지거나 멈춘다. progress(지금까지 찾은 책들)를 주면 단계마다 불러, 중간에 끊겨도
    찾은 데까지는 남게 한다(다시 돌리면 이어서 채운다).
    """
    out: list[dict] = []
    newest = max((int(i) for i in have), default=0)  # 지금까지 본 가장 새 번호

    async def collect(language: str, topic: str, shelf: str | None, limit: int, **how) -> None:
        nonlocal newest
        found: list[dict] = []
        async for results in gutendex_pages(client, language, topic, **how):
            for book in results:
                newest = max(newest, int(book["id"]))
                entry = gutenberg_entry(book)
                if entry and entry["id"] not in have and entry not in found:
                    found.append(entry | ({"genre": shelf} if shelf else {}))
            if len(not_recent(found, newest)) >= limit:
                break
        fresh = not_recent(found, newest)[: max(0, min(limit, need - len(out)))]
        have.update(e["id"] for e in fresh)
        out.extend(fresh)
        print(f"  {topic or language}: +{len(fresh)}")
        if fresh and progress:
            progress(out)

    if need <= 0:
        return []
    # 한국어 책은 몇 권 없고 이 질의는 자주 시간 초과가 난다 — 한 번만 물어보고 넘어간다
    await collect("ko", "", None, need, tries=1, timeout=40)
    await collect("en", "", None, 0)  # 첫 쪽만 읽어 가장 새 번호를 어림한다(갓 올라온 책이 여기 모인다)
    for topic, shelf in TOPICS.items():  # 주제 질의는 무거워 자주 실패한다 — 안 되면 바로 다음으로 넘어간다
        await collect("en", topic, shelf, SHELF_QUOTA, tries=1, timeout=45)
    await collect("en", "", None, need)
    return out


def display_title(book: dict) -> str:
    """유명 고전은 한국어 제목을 앞에, 원제를 괄호에 둔다(영어 제목으로도 검색되게)."""
    ko = KO_TITLES.get(book["id"]) if book["source"] == "gutendex" else None
    if not ko or book["title"].startswith(ko):
        return book["title"]
    return f"{ko} ({re.split(r'[;:]', book['title'])[0].strip()})"


def merge(books: list[dict], new: list[dict]) -> list[dict]:
    """있는 항목 뒤에 새 항목을 보탠다. 같은 (제목, 저자)가 이미 있으면 문서 제목을 그대로 써서 구별한다
    (앱은 제목·저자가 같은 항목을 하나로 합친다 — 시 「진달래꽃」과 시집 『진달래꽃』이 그런 경우)."""

    def key(b: dict) -> tuple[str, str]:
        return (b["title"].strip().lower(), (b.get("author") or "").strip().lower())

    ids = {(b["source"], b["id"]) for b in books}
    names = {key(b) for b in books}
    out = list(books)
    for b in new:
        if (b["source"], b["id"]) in ids:
            continue
        if key(b) in names:
            b = b | {"title": b["id"]} if b["source"] == "wikisource" else b
            if key(b) in names:
                continue
        ids.add((b["source"], b["id"]))
        names.add(key(b))
        out.append(b)
    return out


def dump(books: list[dict]) -> str:
    """한 줄에 한 권 — 들여쓰기보다 작고, 바뀐 책만 diff에 보인다."""
    lines = (json.dumps(b, ensure_ascii=False, separators=(",", ":")) for b in books)
    return "[\n" + ",\n".join(lines) + "\n]\n"


def save(books: list[dict]) -> None:
    for b in books:
        b["title"] = display_title(b)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(dump(books))


async def main(wiki_on: bool = True, gutenberg_on: bool = True) -> None:
    books: list[dict] = json.loads(OUT.read_text()) if OUT.exists() else []
    # 카테고리 수집에서 빠지는 유명작(분류가 덜 달린 문서)은 popular.json의 extra로 보탠다
    extra = json.loads((OUT.parent / "popular.json").read_text())["extra"]
    books = merge(books, extra)
    before = len(books)
    async with httpx.AsyncClient(headers=UA, timeout=30, follow_redirects=True) as client:
        if wiki_on:
            print(f"저자 채움: {await backfill_authors(client, books)}")
            have = {b["id"] for b in books if b["source"] == "wikisource"}
            fresh = await wikisource_new(client, have)
            books = merge(books, fresh)
            print(f"위키문헌: +{len(fresh)}")
            save(books)  # 오래 걸리는 단계마다 적어 둔다 — 중간에 끊겨도 다시 돌리면 이어진다
        if gutenberg_on:
            have = {b["id"] for b in books if b["source"] == "gutendex"}
            need = max(0, GUTENBERG_TARGET - len(have))
            fresh = await gutenberg_new(client, have, need, lambda found: save(merge(books, found)))
            books = merge(books, fresh)
            print(f"구텐베르크: +{len(fresh)}")
    save(books)
    print(f"→ {OUT} ({before} → {len(books)}권)")


if __name__ == "__main__":
    asyncio.run(main(wiki_on="--no-wiki" not in sys.argv, gutenberg_on="--no-gutenberg" not in sys.argv))
