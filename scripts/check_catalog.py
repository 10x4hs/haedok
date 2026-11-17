"""카탈로그의 책이 앱에서 실제로 열리는지 살핀다(본문 없이 저작권 안내만 나오거나 빈 문서인 항목 찾기).

- 위키문헌: 앱의 어댑터로 본문을 받아 본다. 하위 문서까지 따라가도 글이 없으면 "본문 없음".
- 구텐베르크: 평문 파일이 있는지 머리만 물어본다(본 서버 → 미러). 없다고 하면 공식 미러의 그 책 폴더
  목록을 본다 — 폴더는 있는데 평문 파일이 없으면(오디오북, PDF·TeX만 있는 책) "본문 없음".

서버가 잠깐 응답하지 않은 항목은 "확인 못 함"으로만 적고 건드리지 않는다.
두 곳은 서로 다른 서버라 함께 살피되, 한 서버에는 한 번에 하나씩만 묻는다.
실행: python scripts/check_catalog.py [--source wikisource|gutendex] [--sample N] [--ids a,b,c] [--remove]
      --sample N  무작위 N권만 살핀다(같은 날에는 같은 책을 고른다)
      --ids       이 id의 책만 살핀다(쉼표로 구분)
      --remove    "본문 없음"으로 확인된 항목을 catalog.json에서 뺀다
"""

import argparse
import asyncio
import datetime as dt
import json
import random
import re
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.sources.base import SourceError  # noqa: E402
from app.sources.gutendex import GutendexSource  # noqa: E402
from app.sources.wikisource import WikisourceSource  # noqa: E402
from scripts.build_catalog import OUT, UA, dump  # noqa: E402

MIN_CHARS = 20  # 이보다 짧으면 읽을 글이 없는 것으로 본다(스물여덟 자짜리 한시도 글이다)
OK, EMPTY, UNKNOWN = "ok", "본문 없음", "확인 못 함"
MIN_CHARS_NOTE = 300  # 이보다 짧으면 알려만 준다
_LICENSE = re.compile(r"퍼블릭 도메인|Public domain")


async def check_wikisource(source: WikisourceSource, book: dict) -> tuple[str, str]:
    last = ""
    for attempt in range(3):
        try:
            _, text = await source.fetch_book(book["id"])
        except SourceError as e:
            return EMPTY, str(e)
        except (httpx.HTTPError, ValueError) as e:
            last = type(e).__name__
            await asyncio.sleep(3 + attempt * 4)
            continue
        size = len(re.sub(r"\s+", "", text))
        if size < MIN_CHARS:
            return EMPTY, f"{size}자"
        if _LICENSE.search(text[-400:]):
            return OK, "저작권 안내가 본문에 섞임"
        return OK, f"짧은 글 {size}자" if size < MIN_CHARS_NOTE else ""
    return UNKNOWN, last


async def check_gutenberg(client: httpx.AsyncClient, book: dict) -> tuple[str, str]:
    name, seen = f"pg{book['id']}.txt", []
    for base in GutendexSource.MIRRORS:
        try:
            r = await client.head(f"{base}{book['id']}/{name}")
        except httpx.HTTPError as e:
            seen.append(type(e).__name__)
            continue
        if r.status_code == 200:
            return OK, ""
        seen.append(str(r.status_code))
    # 파일이 없다는 답뿐이면 미러의 폴더 목록으로 확정한다: 폴더는 있고 평문 파일만 없는 책은 글이 아니다
    try:
        listing = await client.get(f"{GutendexSource.MIRRORS[-1]}{book['id']}/")
    except httpx.HTTPError:
        return UNKNOWN, "/".join(seen)
    if listing.status_code == 200 and f"Index of /cache/epub/{book['id']}/" in listing.text:
        if name not in listing.text:
            return EMPTY, "평문 파일 없음(오디오북이거나 PDF·TeX만 있는 책)"
    return UNKNOWN, "/".join(seen)


async def main() -> None:
    args = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    args.add_argument("--source", choices=["wikisource", "gutendex"])
    args.add_argument("--sample", type=int)
    args.add_argument("--ids", default="")
    args.add_argument("--remove", action="store_true")
    opt = args.parse_args()

    books: list[dict] = json.loads(OUT.read_text())
    only = {i.strip() for i in opt.ids.split(",") if i.strip()}
    todo = [
        b for b in books if (not opt.source or b["source"] == opt.source) and (not only or b["id"] in only)
    ]
    if opt.sample and opt.sample < len(todo):
        todo = random.Random(dt.date.today().toordinal()).sample(todo, opt.sample)

    results: dict[tuple[str, str], tuple[str, str]] = {}
    async with httpx.AsyncClient(headers=UA, timeout=30, follow_redirects=True) as client:
        wikisource = WikisourceSource(client)

        async def run(source: str) -> None:
            mine = [b for b in todo if b["source"] == source]
            for n, book in enumerate(mine, 1):
                if source == "wikisource":
                    state = await check_wikisource(wikisource, book)
                else:
                    state = await check_gutenberg(client, book)
                results[(source, book["id"])] = state
                if state[0] != OK or state[1]:
                    print(f"{state[0]}: {source} {book['id']} — {book['title']} ({state[1]})", flush=True)
                if n % 100 == 0:
                    print(f"  … {source} {n}/{len(mine)}", flush=True)
                await asyncio.sleep(0.5)  # 서버 배려: 한 번에 하나씩

        await asyncio.gather(run("wikisource"), run("gutendex"))

    count = {s: sum(1 for st, _ in results.values() if st == s) for s in (OK, EMPTY, UNKNOWN)}
    print(f"살핀 책 {len(todo)}권 — 열림 {count[OK]}, 본문 없음 {count[EMPTY]}, 확인 못 함 {count[UNKNOWN]}")
    broken = {key for key, (state, _) in results.items() if state == EMPTY}
    if opt.remove and broken:
        # 살피는 동안 파일이 바뀌었을 수 있으니 지금의 파일에서 뺀다
        now: list[dict] = json.loads(OUT.read_text())
        OUT.write_text(dump([b for b in now if (b["source"], b["id"]) not in broken]))
        print(f"→ {OUT}에서 {len(broken)}권을 뺐다")


if __name__ == "__main__":
    asyncio.run(main())
