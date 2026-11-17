"""위키문헌: 본문이 하위 문서에 나뉜 작품(차례 문서)을 한 권으로 잇는다. 네트워크에는 나가지 않는다."""

import re
from urllib.parse import parse_qs

import httpx
import pytest

from app.sources import wikisource
from app.sources.base import SourceError
from app.sources.wikisource import WikisourceSource, html_to_text, natural_key, subpage_links
from tests.conftest import mock_client

HEADER = (
    '<div class="ws-header ws-noexport noprint"><a href="/wiki/%EC%A0%80%EC%9E%90" title="저자:심훈">심훈</a>'
    '<a href="/wiki/x" title="상록수/제9장">이전</a><p>1935년 동아일보 연재</p></div>'
)
LICENSE = (
    '<div class="licenseContainer licenseBanner"><div class="licensetpl"><p>이 저작물은 저자가 사망한 지 70년이 '
    "지났으므로 퍼블릭 도메인입니다.</p><p>Public domainPublic domainfalsefalse</p></div></div>"
)


def link(title: str, label: str | None = None, new: bool = False) -> str:
    cls = ' class="new"' if new else ""
    return f'<li><a href="/wiki/{title}"{cls} title="{title}">{label or title}</a></li>'


def index_html(title: str, subs: list[str], extra: str = "") -> str:
    toc = "".join(link(f"{title}/{s}", s) for s in subs)
    return f'<div class="mw-parser-output">{HEADER}{extra}<ul>{toc}</ul>{LICENSE}</div>'


class Wiki:
    """작은 가짜 위키문헌: pages = {문서 제목: 본문 HTML}. 부른 호출을 calls에 적어 둔다."""

    def __init__(self, pages: dict[str, str], broken_in_batch: set[str] = frozenset()) -> None:
        self.pages, self.broken, self.calls = pages, broken_in_batch, []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            form = {k: v[0] for k, v in parse_qs(req.content.decode()).items()}
            self.calls.append(("batch", re.findall(r"\{\{:(.+?)\}\}", form["text"])))
            html = '<div class="mw-parser-output">'
            for mark, title in re.findall(r"(HAEDOKPART\d+X)\n\n\{\{:(.+?)\}\}", form["text"]):
                html += f"<p>{mark}\n</p>"
                if title in self.broken:  # 끼워 넣기에 실패하면 문서 대신 링크만 남는다
                    html += f'<p><a href="/wiki/{title}" title="{title}">{title}</a></p>'
                else:
                    html += self.pages.get(title, "")
            return httpx.Response(200, json={"parse": {"title": "API", "text": html + "</div>"}})
        params = req.url.params
        if params["action"] == "query":
            self.calls.append(("prefix", params["apprefix"]))
            found = [{"title": t} for t in self.pages if t.startswith(params["apprefix"])]
            return httpx.Response(200, json={"query": {"allpages": found}})
        self.calls.append(("page", params["page"]))
        if params["page"] not in self.pages:
            return httpx.Response(200, json={"error": {"code": "missingtitle"}})
        return httpx.Response(
            200, json={"parse": {"title": params["page"], "text": self.pages[params["page"]]}}
        )


def chapter(text: str) -> str:
    return f'<div class="mw-parser-output">{HEADER}<p>{text}</p>{LICENSE}</div>'


def test_license_notice_and_header_are_not_body_text():
    assert html_to_text(chapter("가을 학기가 되자")) == "가을 학기가 되자"
    assert html_to_text(f"<div>{HEADER}{LICENSE}</div>") == ""


def test_body_laid_out_in_a_table_is_read_when_nothing_else_is_there():
    notice = '<table class="messagebox"><tr><td><p>관련 글꼴이 설치되지 않은 경우 깨져 보일 수 있습니다.</p></td></tr></table>'
    poem = "<table><tr><td><p>이플 즈ᅀᅳᆷᄒᆞ얏ᄂᆞᆫ 버드리<br>보ᄃᆞ라와</p></td><td><p>隔戶楊柳弱嫋嫋</p></td></tr></table>"
    assert (
        html_to_text(f"<div>{HEADER}{notice}{poem}{LICENSE}</div>")
        == "이플 즈ᅀᅳᆷᄒᆞ얏ᄂᆞᆫ 버드리\n보ᄃᆞ라와\n\n隔戶楊柳弱嫋嫋"
    )
    # 본문이 따로 있으면 표(머리말·차례 틀)는 여전히 본문이 아니다
    assert html_to_text(f"<div>{poem}<p>본문</p></div>") == "본문"


def test_subpage_links_keep_reading_order_and_skip_missing_pages_and_navigation():
    html = (
        f"<div>{HEADER}<ul>{link('상록수/제2장')}{link('상록수/제10장')}{link('상록수/제1장')}"
        f"{link('상록수/제11장', new=True)}{link('탁류/제1장')}{link('상록수/제2장')}</ul>{LICENSE}</div>"
    )
    # 차례에 적힌 순서 그대로(이름순이 아니다), 빨간 링크·다른 작품·머리말의 이전/다음 링크는 뺀다
    assert subpage_links(html, "상록수") == ["상록수/제2장", "상록수/제10장", "상록수/제1장"]


def test_natural_key_sorts_chapter_numbers():
    titles = ["무정/제10장", "무정/제2장", "무정/제1장"]
    assert sorted(titles, key=natural_key) == ["무정/제1장", "무정/제2장", "무정/제10장"]


async def test_index_page_is_assembled_from_subpages_in_toc_order():
    subs = [f"제{n}장" for n in range(1, 12)]  # 11장 → 8개씩 두 번에 나눠 받는다
    pages = {"상록수": index_html("상록수", subs)}
    pages |= {f"상록수/{s}": chapter(f"{s}의 본문입니다. " * 20) for s in subs}
    wiki = Wiki(pages)

    meta, text = await WikisourceSource(mock_client(wiki)).fetch_book("상록수")

    assert meta.title == "상록수" and meta.external_id == "상록수" and meta.has_fulltext
    paragraphs = text.split("\n\n")
    assert paragraphs[0::2] == subs  # 장 제목이 차례 순서대로 (제10장이 제2장 앞에 오지 않는다)
    assert all(body.startswith(f"{s}의 본문입니다.") for s, body in zip(subs, paragraphs[1::2], strict=True))
    assert "퍼블릭 도메인" not in text and "Public domain" not in text and "동아일보" not in text
    batches = [titles for kind, titles in wiki.calls if kind == "batch"]
    assert sorted(len(b) for b in batches) == [3, 8]  # 문서마다 따로 부르지 않는다
    assert [c for c in wiki.calls if c[0] == "page"] == [("page", "상록수")]


async def test_poem_collection_gets_each_poem_title_as_a_heading():
    pages = {
        "진달래꽃 (시집)": index_html("진달래꽃 (시집)", ["먼 후일", "산유화"]),
        "진달래꽃 (시집)/먼 후일": chapter("먼 훗날 당신이 찾으시면<br>그때에 내 말이 잊었노라"),
        "진달래꽃 (시집)/산유화": chapter("산에는 꽃 피네<br>꽃이 피네"),
    }
    _, text = await WikisourceSource(mock_client(Wiki(pages))).fetch_book("진달래꽃 (시집)")
    assert text == (
        "먼 후일\n\n먼 훗날 당신이 찾으시면\n그때에 내 말이 잊었노라\n\n산유화\n\n산에는 꽃 피네\n꽃이 피네"
    )


async def test_long_preface_on_the_index_page_is_kept_but_toc_lines_are_not():
    preface = "<p>" + "머리말입니다. " * 40 + "</p>"
    toc_line = '<p><a href="/wiki/x" title="님의 침묵/군말">군말</a> · 1</p>'  # 문단으로 적힌 차례 한 줄
    pages = {
        "님의 침묵": index_html("님의 침묵", ["군말"], extra=preface + toc_line),
        "님의 침묵/군말": chapter("님만 님이 아니라 기룬 것은 다 님이다"),
    }
    _, text = await WikisourceSource(mock_client(Wiki(pages))).fetch_book("님의 침묵")
    first, heading, body = text.split("\n\n")
    assert first.startswith("머리말입니다.") and heading == "군말" and body.startswith("님만 님이 아니라")


async def test_page_lost_in_a_batch_is_fetched_on_its_own():
    pages = {"탁류": index_html("탁류", ["제1장", "제2장"])}
    pages |= {"탁류/제1장": chapter("금강"), "탁류/제2장": chapter("생활 제일과")}
    wiki = Wiki(pages, broken_in_batch={"탁류/제2장"})
    _, text = await WikisourceSource(mock_client(wiki)).fetch_book("탁류")
    assert text == "제1장\n\n금강\n\n제2장\n\n생활 제일과"
    assert ("page", "탁류/제2장") in wiki.calls and ("page", "탁류/제1장") not in wiki.calls


async def test_single_refetches_are_capped_for_books_full_of_empty_subpages(monkeypatch):
    monkeypatch.setattr(wikisource, "MAX_SINGLE_FETCHES", 2)
    subs = [f"제{n}장" for n in range(1, 7)]
    pages = {"빈 책": index_html("빈 책", subs)} | {f"빈 책/{s}": chapter("") for s in subs[:5]}
    pages["빈 책/제6장"] = chapter("마지막 장만 글이 있다")
    wiki = Wiki(pages)
    _, text = await WikisourceSource(mock_client(wiki)).fetch_book("빈 책")
    assert text == "제6장\n\n마지막 장만 글이 있다"
    assert len([c for c in wiki.calls if c[0] == "page"]) == 1 + 2  # 차례 문서 + 다시 받기 두 번까지만


async def test_nested_index_pages_are_followed_one_more_level():
    pages = {
        "임진록": index_html("임진록", ["상권", "하권"]),
        "임진록/상권": index_html("임진록/상권", ["제1회", "제2회"]),
        "임진록/상권/제1회": chapter("첫 회"),
        "임진록/상권/제2회": chapter("둘째 회"),
        "임진록/하권": chapter("하권 본문"),
    }
    _, text = await WikisourceSource(mock_client(Wiki(pages))).fetch_book("임진록")
    assert text == "상권 · 제1회\n\n첫 회\n\n상권 · 제2회\n\n둘째 회\n\n하권\n\n하권 본문"


async def test_subpage_count_and_total_size_are_capped(monkeypatch):
    subs = [f"제{n}장" for n in range(1, 21)]
    pages = {"긴 책": index_html("긴 책", subs)} | {f"긴 책/{s}": chapter("가" * 100) for s in subs}

    monkeypatch.setattr(wikisource, "MAX_SUBPAGES", 5)
    wiki = Wiki(pages)
    _, text = await WikisourceSource(mock_client(wiki)).fetch_book("긴 책")
    assert text.count("가" * 100) == 5
    assert sum(len(titles) for kind, titles in wiki.calls if kind == "batch") == 5

    monkeypatch.setattr(wikisource, "MAX_SUBPAGES", 400)
    monkeypatch.setattr(wikisource, "MAX_CHARS", 250)
    _, text = await WikisourceSource(mock_client(Wiki(pages))).fetch_book("긴 책")
    assert text.count("가" * 100) == 2  # 상한을 넘기 전까지만 싣는다


async def test_subpages_without_a_toc_are_found_by_prefix_in_natural_order():
    bare = f'<div class="mw-parser-output">{HEADER}{LICENSE}</div>'
    pages = {
        "무정": bare,
        "무정/제10장": chapter("열째"),
        "무정/제2장": chapter("둘째"),
        "무정/제1장": chapter("첫째"),
    }
    wiki = Wiki(pages)
    _, text = await WikisourceSource(mock_client(wiki)).fetch_book("무정")
    assert text == "제1장\n\n첫째\n\n제2장\n\n둘째\n\n제10장\n\n열째"
    assert ("prefix", "무정/") in wiki.calls


async def test_page_with_only_a_license_notice_has_no_body():
    wiki = Wiki({"빈 문서": f'<div class="mw-parser-output">{HEADER}{LICENSE}</div>'})
    with pytest.raises(SourceError, match="no body text"):
        await WikisourceSource(mock_client(wiki)).fetch_book("빈 문서")


async def test_single_page_work_needs_one_call_and_no_license_tail():
    wiki = Wiki({"운수 좋은 날": chapter("새침하게 흐린 품이 눈이 올 듯하더니")})
    _, text = await WikisourceSource(mock_client(wiki)).fetch_book("운수 좋은 날")
    assert text == "새침하게 흐린 품이 눈이 올 듯하더니"
    assert wiki.calls == [("page", "운수 좋은 날")]
