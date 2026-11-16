"""홈·첫 화면 명작 띠(app/data/showcase.json)와 홈 아래 줄들."""

import json
import re
from pathlib import Path

from app.services.home import load_showcase, reading_time
from app.services.search import FULLTEXT_SOURCES
from app.web import SHOWCASE
from tests.conftest import read_chunk, signup
from tests.test_api import upstream

APP = Path(__file__).resolve().parent.parent / "app"
RAW = json.loads((APP / "data" / "showcase.json").read_text())
STILL_PROTECTED = {"1984", "동물농장", "노인과 바다", "데미안", "채식주의자", "이방인", "호밀밭의 파수꾼"}


def csrf(html: str) -> str:
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def test_showcase_is_famous_free_books_with_our_own_covers():
    assert 16 <= len(RAW) <= 24
    titles = [b["title"] for b in RAW]
    assert len(set(titles)) == len(titles)
    assert not STILL_PROTECTED & set(titles)
    assert {"운수 좋은 날", "메밀꽃 필 무렵", "위대한 개츠비", "변신"} <= set(titles)
    for b in RAW:
        assert b["source"] in FULLTEXT_SOURCES and b["id"], b["title"]
        assert b["group"] in ("ko", "world") and b["minutes"] > 0
        assert "publisher" not in b  # 앱이 여는 본문(위키문헌·구텐베르크)은 출판사 판본이 아니다
        assert b["cover"].startswith("/static/covers/home/") and b["cover"].endswith(".webp")
        path = APP / b["cover"].removeprefix("/")
        assert path.is_file() and path.stat().st_size <= 60_000, b["cover"]
        assert (b["lang"] == "en") == (b["source"] == "gutendex")
    assert {b["group"] for b in RAW} == {"ko", "world"}
    assert len(SHOWCASE) == len(RAW) and all(b["readable"] for b in SHOWCASE)


def test_loader_drops_books_it_cannot_open_or_show(tmp_path):
    ok = {"source": "wikisource", "id": "봄봄", "title": "봄봄", "author": "김유정", "group": "ko",
          "lang": "ko", "minutes": 23, "cover": RAW[0]["cover"]}  # fmt: skip
    data = [
        ok,
        ok | {"source": "aladin"},  # 본문을 줄 수 없는 소스
        ok | {"cover": "/static/covers/home/nope.webp"},  # 표지 파일이 없다
        ok | {"group": "store"},
    ]
    f = tmp_path / "s.json"
    f.write_text(json.dumps(data, ensure_ascii=False))
    out = load_showcase(f)
    assert len(out) == 1 and out[0]["readable"] and out[0]["time"] == "약 25분"


def test_reading_time_is_rounded_for_humans():
    assert reading_time(1) == "1분"
    assert reading_time(17) == "약 15분"
    assert reading_time(204) == "약 3시간 20분"
    assert reading_time(118) == "약 2시간"


async def test_landing_strip_only_links_readable_books_with_our_covers(api):
    async with api(upstream) as c:
        landing = await c.get("/")
    html = landing.text
    assert landing.status_code == 200
    assert "/static/covers/showcase/" not in html and "data-locked title=" not in html
    assert 'href="/open/wikisource/%EC%9A%B4%EC%88%98%20%EC%A2%8B%EC%9D%80%20%EB%82%A0"' in html


async def test_home_carousel_and_curation_rows(api):
    async with api(upstream) as c:
        await signup(c, nickname="지현")
        r = await c.get("/")
    html = r.text
    assert r.status_code == 200 and "지현님" in html and "오늘은 뭘 읽을까요" in html
    assert html.count('class="hm-book') == len(SHOWCASE)
    main = html[html.index("<main") : html.index("</main>")]
    assert main.count("btn primary") == 1 and "바로 읽기" in main  # 화면의 주 버튼은 하나
    first = SHOWCASE[0]
    assert f'id="hmTitle">{first["title"]}<' in html
    assert "한국 문학 대표작" in html and "세계 고전" in html and "영어 원문" in html
    # 출판사(서점) 표지는 홈에 쓰지 않는다
    assert "/static/covers/showcase/" not in html and "aladin" not in html and "/store/" not in html
    assert "이어 읽기" not in html and "친구들이 읽는 중" not in html  # 아직 기록이 없다
    assert "/static/css/home.css" in html and "/static/home.js" in html


async def test_home_shows_continue_and_friends_reading(api, clock):
    async with api(upstream) as host, api(upstream) as friend:
        await signup(host, email="host@example.com", nickname="호스트")
        await signup(friend, email="friend@example.com", nickname="친구")
        page = await host.get("/races")
        race_url = (
            await host.post("/races/start/wikisource/긴글", data={"csrf": csrf(page.text), "days": "3"})
        ).headers["location"]
        race = await host.get(race_url)
        code = re.search(r'id="code">([A-Z0-9]{6})<', race.text).group(1)
        preview = await friend.get(f"/join/{code}")
        await friend.post(f"/join/{code}", data={"csrf": csrf(preview.text)})
        book_id = re.search(r'href="/books/(\d+)"', race.text).group(1)
        reader = await friend.get(f"/books/{book_id}")
        token = reader.text.split('data-csrf="')[1].split('"')[0]
        await read_chunk(friend, book_id, 0, token, clock)
        friend_home = (await friend.get("/")).text
        host_home = (await host.get("/")).text
    assert (
        "이어 읽기" in friend_home
        and f'href="/books/{book_id}"' in friend_home
        and "% 읽었어요" in friend_home
    )
    assert "친구들이 읽는 중" in host_home and "hm-av" in host_home and ">친구<" in host_home


async def test_opening_a_poem_from_a_collection_uses_its_own_title(api):
    async with api(upstream) as c:
        await signup(c)
        r = await c.get("/open/wikisource/하늘과 바람과 별과 시 (1948년)/서시")
        reader = await c.get(r.headers["location"])
    assert r.status_code == 303 and "<title>서시" in reader.text and "(1948년)/서시" not in reader.text
