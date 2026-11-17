import asyncio
import re
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup

from app.sources.base import BookMeta, BookSource, SourceError

# 본문이 아닌 요소: 편집 링크, 각주, 표(머리말 틀), 목차, 인쇄 제외 영역, 저작권 상자, 다른 문서·글꼴 안내
_NOISE = (
    ".mw-editsection, sup.reference, ol.references, .reference, table, style, script, "
    ".noprint, #toc, .toc, .ws-noexport, .ws-header, .licenseContainer, .licensetpl, .hatnote, .subNote, "
    ".messagebox"
)
_NOISE_BUT_TABLES = _NOISE.replace(" table,", "")
# 하위 문서 링크를 찾을 때는 표 안의 차례도 봐야 하므로 머리말·저작권 상자만 뺀다
_NOT_CONTENT = ".ws-header, .licenseContainer, .licensetpl, .hatnote, .subNote, .mw-editsection"

MAX_SUBPAGES = 400  # 한 권으로 잇는 하위 문서 수의 상한
MAX_CHARS = 3_000_000  # 한 권 본문의 상한(글자) — 넘으면 거기서 멈춘다
MAX_DEPTH = 2  # 「책/제1편/제1장」처럼 두 단계까지 따라간다
BATCH = 8  # 한 번의 API 호출로 받아 오는 하위 문서 수
CONCURRENCY = 3  # 위키미디어 서버 배려: 동시에 보내는 호출 수
MIN_OWN_CHARS = 300  # 차례 문서에 이만큼 글이 있으면 머리글로 보고 앞에 싣는다
MAX_SINGLE_FETCHES = 8  # 한꺼번에 받지 못한 하위 문서를 하나씩 다시 받는 횟수의 상한
SUBPAGE_TIMEOUT = 20.0  # 여러 문서를 한꺼번에 받는 호출은 기본 3초로는 모자랄 수 있다

_MARK = "HAEDOKPART{}X"
_MARK_SPLIT = re.compile(r"HAEDOKPART(\d+)X")
_DIGITS = re.compile(r"(\d+)")


def _soup_text(soup: BeautifulSoup, skip_link_only: bool = False, keep_tables: bool = False) -> str:
    for el in soup.select(_NOISE_BUT_TABLES if keep_tables else _NOISE):
        el.decompose()
    for br in soup.find_all("br"):
        br.replace_with("\n")
    paragraphs = []
    for block in soup.find_all(["p", "dd"]):
        if skip_link_only and block.find("a") and not _text_outside_links(block):
            continue  # 차례의 한 줄(링크뿐인 문단)은 본문이 아니다
        lines = (" ".join(line.split()) for line in block.get_text().split("\n"))
        para = "\n".join(line for line in lines if line)
        if para:
            paragraphs.append(para)
    return "\n\n".join(paragraphs)


def _text_outside_links(block) -> str:
    """링크 밖에 적힌 글 — 차례의 번호·점·줄표는 글로 치지 않는다."""
    loose = "".join(s for s in block.find_all(string=True) if s.find_parent("a") is None)
    return loose.strip(" \n\t·-–—.,0123456789")


def html_to_text(html: str) -> str:
    if text := _soup_text(BeautifulSoup(html, "html.parser")):
        return text
    # 본문이 표 안에 짜여 있는 문서(원문과 풀이를 나란히 둔 한시 등)는 표를 남기고 다시 읽는다
    return _soup_text(BeautifulSoup(html, "html.parser"), skip_link_only=True, keep_tables=True)


def subpage_links(html: str, title: str) -> list[str]:
    """문서 안에서 가리키는 하위 문서(「제목/…」) 제목을 읽는 순서대로, 겹치지 않게 돌려준다."""
    soup = BeautifulSoup(html, "html.parser")
    for el in soup.select(_NOT_CONTENT):
        el.decompose()
    prefix, out = f"{title}/", []
    for a in soup.find_all("a", href=True):
        target = a.get("title") or ""
        if "new" in (a.get("class") or []) or not target.startswith(prefix):  # 아직 없는 문서(빨간 링크) 제외
            continue
        if target not in out:
            out.append(target)
    return out


def natural_key(title: str) -> list:
    """「제10장」이 「제2장」 뒤에 오도록 숫자는 숫자로 견준다."""
    return [int(p) if p.isdigit() else p for p in _DIGITS.split(title)]


class WikisourceSource(BookSource):
    """위키문헌 MediaWiki API — 한국 근대문학 퍼블릭 도메인 전문. external_id = 문서 제목.

    장편·시집처럼 본문이 하위 문서(「상록수/제1장」 …)에 나뉘어 있고 문서 자체는 차례뿐인 작품은
    하위 문서를 차례 순서대로 받아 한 권으로 잇는다.
    """

    name = "wikisource"
    fulltext = True

    def __init__(self, client: httpx.AsyncClient, lang: str = "ko") -> None:
        super().__init__(client)
        self.lang = lang
        self.api = f"https://{lang}.wikisource.org/w/api.php"

    async def search(self, query: str, limit: int = 10) -> list[BookMeta]:
        data = await self._get_json(
            self.api,
            params={
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srnamespace": 0,
                "srlimit": min(limit, 50),
                "format": "json",
                "formatversion": 2,
            },
        )
        return [self._meta(hit["title"]) for hit in data.get("query", {}).get("search", [])]

    async def fetch_book(self, external_id: str) -> tuple[BookMeta, str]:
        data = await self._get_json(
            self.api,
            params={
                "action": "parse",
                "page": external_id,
                "prop": "text",
                "redirects": 1,
                "format": "json",
                "formatversion": 2,
            },
        )
        if "error" in data:
            raise SourceError(f"wikisource: {data['error'].get('code', 'unknown')}")
        title, html = data["parse"]["title"], data["parse"]["text"]
        subpages, depth = subpage_links(html, title), 1
        if not subpages:
            if text := html_to_text(html):
                return self._meta(title), text
            # 차례 없이 하위 문서만 있는 문서: 이름순으로 모두 받는다(더 깊이 들어가지 않는다)
            subpages, depth = await self._subpages_by_prefix(title), MAX_DEPTH
        budget = {"pages": MAX_SUBPAGES, "retries": MAX_SINGLE_FETCHES}
        sections = await self._collect(subpages, depth, budget) if subpages else []
        if not sections:
            if text := html_to_text(html):  # 하위 문서가 모두 비었으면 문서 자체의 글이라도
                return self._meta(title), text
            raise SourceError(f"wikisource page {external_id!r} has no body text")
        own = _soup_text(BeautifulSoup(html, "html.parser"), skip_link_only=True)
        parts = [own] if len(own) >= MIN_OWN_CHARS else []
        size = sum(len(p) for p in parts)
        for sub, text in sections:
            if parts and size + len(text) > MAX_CHARS:
                break
            parts.append(f"{sub[len(title) + 1 :].replace('/', ' · ')}\n\n{text}")
            size += len(text)
        return self._meta(title), "\n\n".join(parts)

    async def _collect(self, titles: list[str], depth: int, budget: dict[str, int]) -> list[tuple[str, str]]:
        """하위 문서들의 (제목, 본문)을 주어진 순서대로. 그 문서도 차례뿐이면 한 단계 더 들어간다."""
        titles = titles[: max(budget["pages"], 0)]
        budget["pages"] -= len(titles)
        batches = [titles[i : i + BATCH] for i in range(0, len(titles), BATCH)]
        gate = asyncio.Semaphore(CONCURRENCY)

        async def one(batch: list[str]) -> dict[str, str]:
            async with gate:
                return await self._render(batch)

        rendered: dict[str, str] = {}
        for got in await asyncio.gather(*(one(b) for b in batches)):
            rendered |= got
        out: list[tuple[str, str]] = []
        for title in titles:
            html = rendered.get(title, "")
            text = html_to_text(html)
            if text in ("", title) and budget["retries"] > 0:
                # 한꺼번에 받다가 빠졌거나 크기 제한에 걸린 문서는 따로 받는다.
                # 빈 문서가 많은 책에서 끝없이 되묻지 않게 몇 번까지만
                budget["retries"] -= 1
                html = await self._page_html(title)
                text = html_to_text(html)
            if text == title:
                text = ""  # 끼워 넣지 못해 링크만 남은 것은 본문이 아니다
            nested = subpage_links(html, title) if depth < MAX_DEPTH else []
            if nested and len(text) < MIN_OWN_CHARS:
                out += await self._collect(nested, depth + 1, budget)
            elif text:
                out.append((title, text))
        return out

    async def _render(self, titles: list[str]) -> dict[str, str]:
        """여러 문서를 한 번의 parse 호출로 받는다: 문서 사이에 표식을 넣고 끼워 넣기({{:문서}})로 펼친다."""
        wikitext = "".join(f"\n\n{_MARK.format(i)}\n\n{{{{:{t}}}}}" for i, t in enumerate(titles))
        resp = await self.client.post(
            self.api,
            data={
                "action": "parse",
                "text": wikitext,
                "contentmodel": "wikitext",
                "prop": "text",
                "disablelimitreport": 1,
                "format": "json",
                "formatversion": 2,
            },
            timeout=SUBPAGE_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise SourceError(f"wikisource: {data['error'].get('code', 'unknown')}")
        pieces = _MARK_SPLIT.split(data["parse"]["text"])
        return {titles[int(pieces[i])]: pieces[i + 1] for i in range(1, len(pieces) - 1, 2)}

    async def _page_html(self, title: str) -> str:
        resp = await self.client.get(
            self.api,
            params={
                "action": "parse",
                "page": title,
                "prop": "text",
                "redirects": 1,
                "format": "json",
                "formatversion": 2,
            },
            timeout=SUBPAGE_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        return "" if "error" in data else data["parse"]["text"]

    async def _subpages_by_prefix(self, title: str) -> list[str]:
        data = await self._get_json(
            self.api,
            params={
                "action": "query",
                "list": "allpages",
                "apprefix": f"{title}/",
                "apnamespace": 0,
                "apfilterredir": "nonredirects",
                "aplimit": MAX_SUBPAGES,
                "format": "json",
                "formatversion": 2,
            },
        )
        pages = [p["title"] for p in data.get("query", {}).get("allpages", [])]
        return sorted(pages, key=natural_key)

    def _meta(self, title: str) -> BookMeta:
        return BookMeta(
            source=self.name,
            external_id=title,
            title=title,
            language=self.lang,
            has_fulltext=True,
            link=f"https://{self.lang}.wikisource.org/wiki/{quote(title.replace(' ', '_'))}",
        )
