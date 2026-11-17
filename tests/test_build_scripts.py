"""서가·서점 데이터를 만드는 스크립트의 판단 로직. 네트워크에는 나가지 않는다."""

import json

import httpx

from scripts import build_catalog, build_store
from scripts.build_store import Pick
from tests.conftest import mock_client


def box(item_id, title, author=None, publisher="더클래식", kind="전자책", translator="옮긴이"):
    who = (
        f'<a href="/Search/wSearchResult.aspx?AuthorSearch={author}@1">{author}</a> (지은이), '
        if author
        else ""
    )
    return (
        f'<div class="ss_book_box" itemId="{item_id}"><img src="https://image.aladin.co.kr/product/1/2/cover150/e{item_id}_1.jpg" />'
        f'<span class="tit_category">[{kind}]</span>&nbsp;<a href="x" class="bo3">{title}</a>'
        f'<li>{who}<a href="/Search/wSearchResult.aspx?AuthorSearch={translator}@2">{translator}</a> (옮긴이) | '
        f'<A href="/search/wsearchresult.aspx?PublisherSearch=x@3" >{publisher}</A> | 2013년</li></div>'
    )


def page(*boxes: str) -> str:
    return "<html>" + "".join(boxes) + "</html>"


# --- 서점(build_store) ---


def test_store_prefers_the_wanted_publisher_then_exact_title():
    found = build_store.candidates(
        page(
            box("1", "폭풍의 언덕 (한글판)", "에밀리 브론테"),
            box("2", "폭풍의 언덕", "에밀리 브론테", publisher="문학동네"),
            box("3", "폭풍의 언덕", "에밀리 브론테", publisher="민음사"),
        )
    )
    assert build_store.choose(found, Pick("폭풍의 언덕", "폭풍의 언덕", "브론테", "민음사"))["id"] == "3"
    assert build_store.choose(found, Pick("폭풍의 언덕", "폭풍의 언덕", "브론테"))["id"] == "2"
    book = found[2]
    assert book["cover"] == "https://image.aladin.co.kr/product/1/2/cover500/e3_1.jpg"  # 큰 표지로 바꾼다
    assert book["url"] == "https://www.aladin.co.kr/shop/wproduct.aspx?ItemId=3&start=pebook"


def test_store_rejects_wrong_author_audiobooks_samples_and_later_volumes():
    found = build_store.candidates(
        page(
            box("1", "난장이가 쏘아올린 작은 공", "홍파"),  # 같은 제목의 다른 책
            box("2", "레 미제라블 2", "빅토르 위고"),
            box("3", "레 미제라블 (체험판)", "빅토르 위고"),
            box("4", "레 미제라블 1", "빅토르 위고", kind="오디오북"),
            box("5", "레 미제라블 1~5 세트", "빅토르 위고"),
            box("6", "양을 쫓는 모험 - 하", "무라카미 하루키"),
            box("7", "양을 쫓는 모험 - 상", "무라카미 하루키"),
            box("8", "레 미제라블 해설", "빅토르 위고"),
            box("9", "레 미제라블 1", "빅토르 위고"),
        )
    )
    assert "4" not in {b["id"] for b in found}  # 오디오북은 후보에도 없다
    assert build_store.choose(found, Pick("난장이", "난장이가 쏘아올린 작은 공", "조세희")) is None
    assert build_store.choose(found, Pick("레 미제라블", "레 미제라블", "위고"))["id"] == "9"
    assert build_store.choose(found, Pick("양을 쫓는 모험", "양을 쫓는 모험", "하루키"))["id"] == "7"
    # 지은이를 확인하지 않던 예전 목록 방식(두 칸짜리)도 그대로 돈다
    assert build_store.choose(found, Pick("난장이", "난장이가 쏘아올린 작은 공"))["id"] == "1"


def test_store_presence_needs_the_author_too():
    books = [{"id": "1", "title": "죽음의 수용소에서 모든 것을 잃어도", "author": "김프로랩"}]
    assert build_store.present(books, Pick("죽음의 수용소에서", "죽음의 수용소에서"))
    assert not build_store.present(books, Pick("죽음의 수용소에서", "죽음의 수용소에서", "프랭클"))


async def test_store_find_reads_author_from_the_product_page_and_falls_back_without_publisher(monkeypatch):
    monkeypatch.setattr(build_store, "PAUSE", 0)
    asked = []

    def handler(req: httpx.Request) -> httpx.Response:
        word = req.url.params.get("SearchWord", "")
        asked.append(word or f"상품 {req.url.params['ItemId']}")
        if "ItemId" in req.url.params:
            return httpx.Response(200, text='<meta name="author" content="니코스 카잔자키스, 김욱동" />')
        if word == "그리스인 조르바 민음사":
            return httpx.Response(
                200, text=page(box("7", "그리스인 조르바", None, "민음사", translator="김욱동"))
            )
        if word == "월든":
            return httpx.Response(200, text=page(box("8", "월든", "헨리 데이비드 소로", "은행나무")))
        return httpx.Response(200, text=page())

    client = mock_client(handler)
    zorba = await build_store.find(client, Pick("그리스인 조르바", "그리스인 조르바", "카잔", "민음사"))
    assert zorba == {
        "id": "7",
        "title": "그리스인 조르바",
        "author": "니코스 카잔자키스",  # 검색 결과에는 옮긴이만 있어 상품 페이지에서 읽었다
        "cover": "https://image.aladin.co.kr/product/1/2/cover500/e7_1.jpg",
        "price": None,
        "url": "https://www.aladin.co.kr/shop/wproduct.aspx?ItemId=7&start=pebook",
    }
    walden = await build_store.find(client, Pick("월든", "월든", "소로", "민음사"))
    assert walden["id"] == "8" and walden["author"] == "헨리 데이비드 소로"
    assert asked == ["그리스인 조르바 민음사", "상품 7", "월든 민음사", "월든"]
    assert await build_store.find(client, Pick("없는 책", "없는 책", "아무개")) is None


def test_store_pick_list_has_no_repeated_new_titles():
    more = [Pick(*p) for p in build_store.MORE]
    assert len(more) >= 120 and all(p.author for p in more)  # 새로 넣는 책은 지은이까지 확인한다
    keys = [(p.must, p.author) for p in more]
    assert len(keys) == len(set(keys))


# --- 서가(build_catalog) ---


def test_public_domain_guard_keeps_the_1962_rule():
    pd = build_catalog.is_public_domain
    assert pd(["분류:PD-old-70", "분류:단편소설"], "단편소설", None)
    assert pd(["분류:PD-old-50"], None, 1950)  # 이광수·채만식처럼 1962년 이전에 사망
    assert not pd(["분류:PD-old-50"], None, 1963)  # 염상섭 — 2013년 연장 뒤에도 보호 중
    assert not pd(["분류:PD-old-50"], None, None)  # 사망 연도를 모르면 싣지 않는다
    assert not pd(["분류:PD-old-70"], None, 1984)  # 표시가 있어도 사망 연도가 맞지 않으면 뺀다
    assert not pd(["분류:장편소설"], "장편소설", None)  # 아무 표시도 없는 근대 소설
    assert pd(["분류:고전소설"], "고전소설", None)  # 조선 시대 고전은 그 자체로 퍼블릭 도메인
    assert not pd(["분류:PD-old-95-US"], None, None)  # 미국 기준 표시만으로는 모자라다


def test_author_and_index_detection_from_wikitext():
    assert build_catalog.author_of("{{머리말\n|제목 = 흙\n|지은이 = [[저자:이광수|이광수]]\n}}") == "이광수"
    assert build_catalog.author_of("|글쓴이 = [[글쓴이:김만중|김만중]]") == "김만중"
    assert build_catalog.author_of("| 저자 = [[저자:김소월|김소월(김정식)]]") == "김소월"
    assert build_catalog.author_of("| 저자 = 미상") is None
    assert build_catalog.is_index("* [[/제1장|제1장]]", "상록수")
    assert build_catalog.is_index("* [[흙/제1장|제1장]]", "흙")
    assert build_catalog.is_index('<pages index="x.pdf" from=11 to=12 />', "추월색")
    assert not build_catalog.is_index("새침하게 흐린 품이 [[눈]]이 올 듯하더니", "운수 좋은 날")


def test_translator_is_read_from_the_header():
    assert build_catalog.translator_of(
        "|저자 = [[저자:루쉰|루쉰]]\n|역자 = [[저자:이육사|이육사]]\n|부제 ="
    ) == (True, "이육사")
    assert build_catalog.translator_of("|지은이 = 도요시마 요시오\n|역자 = 김해향 \n|부제 =") == (
        True,
        None,
    )  # 누구인지 모른다
    assert build_catalog.translator_of("|저자 = [[저자:김유정|김유정]]\n|역자 =\n|부제 = ") == (False, None)
    assert build_catalog.translator_of("|저자 = [[저자:김유정|김유정]]") == (False, None)


async def test_describe_skips_unverifiable_translations_and_unknown_authors(monkeypatch):
    async def no_wait(_):
        return None

    monkeypatch.setattr(build_catalog.asyncio, "sleep", no_wait)
    monkeypatch.setattr(build_catalog, "_deaths", {})
    body = "가나다라 마바사 " * 200
    pages = {
        "봄봄": (
            "|저자 = [[저자:김유정|김유정]]\n}}" + body,
            ["분류:PD-old-70", "분류:단편소설", "분류:1935년 작품"],
        ),
        "삼대": ("|저자 = [[저자:염상섭|염상섭]]\n}}" + body, ["분류:PD-old-50", "분류:장편소설"]),
        "눈의 여왕": (
            "|저자 = [[저자:안데르센|안데르센]]\n}}" + body,
            ["분류:PD-old-100", "분류:번역 저작권 정보 없음"],
        ),
        "맥베스": ("|저자=윌리엄 셰익스피어\n}}\n*[[맥베스/제1막|제1막]]", ["분류:PD-old-100", "분류:희곡"]),
        "고향 (루쉰)": (
            "|저자 = [[저자:루쉰|루쉰]]\n|역자 = [[저자:이육사|이육사]]\n}}" + body,
            ["분류:PD-old-70"],
        ),
        "새 번역": (
            "|저자 = [[저자:루쉰|루쉰]]\n|역자 = [[저자:요즘 사람|요즘 사람]]\n}}" + body,
            ["분류:PD-old-70"],
        ),
        "심청전": ("|저자 =\n}}" + body, ["분류:PD-old-100", "분류:판소리 소설"]),
        "상록수": ("|저자 = [[저자:심훈|심훈]]\n}}\n* [[/제1장|제1장]]", ["분류:PD-old-70", "분류:장편소설"]),
    }
    died = {
        "저자:김유정": 1937,
        "저자:염상섭": 1963,
        "저자:안데르센": 1875,
        "저자:루쉰": 1936,
        "저자:이육사": 1944,
        "저자:심훈": 1936,
    }

    def handler(req: httpx.Request) -> httpx.Response:
        titles = req.url.params["titles"].split("|")
        if req.url.params["prop"] == "categories":  # 저자 문서의 사망 연도
            year = died.get(titles[0])
            cats = [{"title": f"분류:{year}년 죽음"}] if year else []
            return httpx.Response(200, json={"query": {"pages": [{"title": titles[0], "categories": cats}]}})
        found = [
            {
                "title": t,
                "categories": [{"title": c} for c in pages[t][1]],
                "revisions": [{"slots": {"main": {"content": "{{머리말\n" + pages[t][0]}}}],
            }
            for t in titles
        ]
        return httpx.Response(200, json={"query": {"pages": found}})

    got = await build_catalog.describe(mock_client(handler), list(pages), "단편소설")
    assert [(b["id"], b["author"], b["genre"]) for b in got] == [
        ("봄봄", "김유정", "단편소설"),
        ("고향 (루쉰)", "루쉰", "단편소설"),  # 옮긴이 이육사도 1944년에 사망
        ("심청전", None, "고전소설"),  # 저자 미상의 고전
        ("상록수", "심훈", "장편소설"),  # 차례뿐인 문서 — 본문은 하위 문서에 있다
    ]
    assert got[0]["year"] == 1935 and got[1]["title"] == "고향"


def test_merge_keeps_existing_entries_and_tells_same_named_works_apart():
    poem = {
        "source": "wikisource",
        "id": "진달래꽃",
        "title": "진달래꽃",
        "author": "김소월",
        "cover": "/c.jpg",
    }
    book = {"source": "wikisource", "id": "진달래꽃 (시집)", "title": "진달래꽃", "author": "김소월"}
    again = dict(poem, title="바뀐 제목")
    merged = build_catalog.merge([poem], [book, again, dict(book)])
    assert merged[0] is poem and len(merged) == 2  # 있는 항목은 손대지 않는다(표지·주소 유지)
    assert merged[1]["title"] == "진달래꽃 (시집)"  # 앱이 제목·저자가 같은 둘을 하나로 합치지 않게


def test_gutenberg_entries_are_english_text_books_on_a_fitting_shelf():
    book = {
        "id": 1661,
        "title": "The Adventures of\r\n  Sherlock Holmes",
        "authors": [{"name": "Doyle, Arthur Conan"}],
        "languages": ["en"],
        "media_type": "Text",
        "subjects": ["Detective and mystery stories, English"],
        "bookshelves": ["Category: Crime, Thrillers and Mystery"],
        "formats": {"text/plain; charset=utf-8": "https://www.gutenberg.org/ebooks/1661.txt.utf-8"},
    }
    assert build_catalog.gutenberg_entry(book) == {
        "source": "gutendex",
        "id": "1661",
        "title": "The Adventures of Sherlock Holmes",
        "author": "Arthur Conan Doyle",
        "year": None,
        "genre": "세계 · 추리",
        "lang": None,
    }
    assert build_catalog.gutenberg_entry(book | {"media_type": "Sound"}) is None
    assert build_catalog.gutenberg_entry(book | {"languages": ["fr"]}) is None
    assert build_catalog.gutenberg_entry(book | {"formats": {"application/epub+zip": "x.epub"}}) is None
    assert build_catalog.gutenberg_entry(book | {"formats": {"text/plain": "https://g/1661.zip"}}) is None
    assert build_catalog.gutenberg_entry(book | {"title": "Webster's Unabridged Dictionary"}) is None
    assert build_catalog.gutenberg_entry(book | {"languages": ["ko"]})["lang"] == "ko"
    plain = book | {"subjects": ["Illegitimate children -- Fiction"], "bookshelves": []}
    assert (
        build_catalog.gutenberg_entry(plain)["genre"] == "세계 고전"
    )  # 'children'만으로 어린이 서가에 꽂지 않는다


async def test_gutenberg_top_up_goes_shelf_by_shelf_and_skips_fresh_uploads_and_adult_books(monkeypatch):
    async def no_wait(_):
        return None

    monkeypatch.setattr(build_catalog.asyncio, "sleep", no_wait)
    monkeypatch.setattr(build_catalog, "TOPICS", {"detective": "세계 · 추리"})
    monkeypatch.setattr(build_catalog, "SHELF_QUOTA", 1)

    def book(gid, title, subjects=(), **extra):
        return {
            "id": gid,
            "title": title,
            "authors": [{"name": "Doe, Jane"}],
            "languages": ["en"],
            "media_type": "Text",
            "subjects": list(subjects),
            "bookshelves": [],
            "formats": {"text/plain; charset=utf-8": f"https://g/{gid}.txt"},
        } | extra

    popular = [
        book(79900, "Uploaded last week"),  # 갓 올라와 잠깐 순위에 든 책
        book(1342, "Pride and Prejudice"),  # 이미 있는 책
        book(30254, "A Victorian Novel", ["Erotic fiction"]),
        book(27509, "The 2006 CIA World Factbook"),
        book(2600, "War and Peace : $b a novel"),
        book(20000, "Twenty Thousand Leagues (audio)", media_type="Sound"),
        book(98, "A Tale of Two Cities"),
    ]
    detective = [
        book(79901, "Fresh Mystery"),
        book(1661, "Sherlock Holmes"),
        book(863, "The Mysterious Affair"),
    ]
    asked = []

    def handler(req: httpx.Request) -> httpx.Response:
        params = dict(req.url.params)
        asked.append((params["languages"], params.get("topic", "")))
        if params["languages"] == "ko":
            return httpx.Response(503)
        return httpx.Response(
            200, json={"results": detective if params.get("topic") else popular, "next": None}
        )

    got = await build_catalog.gutenberg_new(mock_client(handler), {"1342"}, need=3)
    assert [(b["id"], b["title"], b["genre"]) for b in got] == [
        ("1661", "Sherlock Holmes", "세계 · 추리"),  # 서가마다 정해진 수만큼 먼저
        ("2600", "War and Peace: a novel", "세계 고전"),  # 그다음 전체 인기 순서로 채운다
        ("98", "A Tale of Two Cities", "세계 고전"),
    ]
    assert asked[0] == ("ko", "") and ("en", "detective") in asked
    assert await build_catalog.gutenberg_new(mock_client(handler), {"1342"}, need=0) == []


def test_famous_classics_get_a_korean_title_first_once():
    book = {"source": "gutendex", "id": "84", "title": "Frankenstein; or, The modern Prometheus"}
    assert build_catalog.display_title(book) == "프랑켄슈타인 (Frankenstein)"
    book["title"] = build_catalog.display_title(book)
    assert build_catalog.display_title(book) == "프랑켄슈타인 (Frankenstein)"  # 다시 돌려도 겹쳐 붙지 않는다
    other = {"source": "wikisource", "id": "84", "title": "팔십사"}
    assert build_catalog.display_title(other) == "팔십사"


def test_catalog_dump_is_one_book_per_line_and_round_trips():
    books = [
        {"source": "wikisource", "id": '따옴표 "글"', "title": "글", "author": None},
        {"source": "gutendex", "id": "1"},
    ]
    text = build_catalog.dump(books)
    assert json.loads(text) == books and len(text.splitlines()) == len(books) + 2


# --- 점검(check_catalog) ---


async def test_checker_tells_missing_text_from_a_server_that_is_only_down(monkeypatch):
    from scripts import check_catalog as cc

    async def no_wait(_):
        return None

    monkeypatch.setattr(cc.asyncio, "sleep", no_wait)
    license_only = '<div class="licenseContainer"><p>이 저작물은 퍼블릭 도메인입니다.</p></div>'

    def handler(req: httpx.Request) -> httpx.Response:
        host, path = req.url.host, req.url.path
        if host == "ko.wikisource.org":
            page = req.url.params.get("page")
            if req.url.params["action"] == "query":
                return httpx.Response(200, json={"query": {"allpages": []}})
            if page == "먹통":
                return httpx.Response(503)
            body = "<p>" + "봄이 왔다. " * 30 + "</p>" if page == "봄" else license_only
            return httpx.Response(200, json={"parse": {"title": page, "text": body}})
        if host == "gutenberg.pglaf.org":
            folder = path.removeprefix("/cache/epub/").split("/")[0]
            if path.endswith(".txt"):
                return httpx.Response(200 if folder in ("1", "4") else 404)
            if folder == "2":  # 오디오북: 폴더는 있지만 평문 파일이 없다
                return httpx.Response(
                    200, html='Index of /cache/epub/2/ <a href="2-cover.png">2-cover.png</a>'
                )
            if folder == "3":  # 목록에는 평문이 보이는데 받지는 못했다 → 단정하지 않는다
                return httpx.Response(200, html='Index of /cache/epub/3/ <a href="pg3.txt">pg3.txt</a>')
            return httpx.Response(404)
        return httpx.Response(503)  # 본 서버 점검 중

    client = mock_client(handler)
    wiki = cc.WikisourceSource(client)
    assert await cc.check_wikisource(wiki, {"id": "봄"}) == (cc.OK, "짧은 글 150자")
    assert (await cc.check_wikisource(wiki, {"id": "안내뿐"}))[0] == cc.EMPTY  # 저작권 안내만 있는 문서
    assert (await cc.check_wikisource(wiki, {"id": "먹통"}))[0] == cc.UNKNOWN  # 서버 오류로는 지우지 않는다
    assert await cc.check_gutenberg(client, {"id": "1"}) == (cc.OK, "")  # 본 서버가 점검 중이어도 미러에 있다
    assert (await cc.check_gutenberg(client, {"id": "2"}))[0] == cc.EMPTY  # 오디오북
    assert (await cc.check_gutenberg(client, {"id": "3"}))[0] == cc.UNKNOWN
    assert (await cc.check_gutenberg(client, {"id": "5"}))[0] == cc.UNKNOWN  # 폴더조차 확인하지 못했다
