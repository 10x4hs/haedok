"""서가 책의 대략적인 분량(글자 수)을 모아 app/data/lengths.json으로 저장한다.

본문을 통째로 받지 않고 크기만 본다 — 탐색 화면의 '약 1시간 40분' 같은 예상 시간에 쓰는 어림값이다.
- 위키문헌: API의 문서 길이(위키텍스트 바이트) ÷ 3 ≈ 한글 글자 수
- 구텐베르크: 평문 파일 크기(Range 요청의 Content-Range) − 라이선스 머리말·꼬리말 ≈ 글자 수

    .venv/bin/python scripts/build_lengths.py
"""

import asyncio
import json
from pathlib import Path

import httpx

DATA = Path(__file__).resolve().parent.parent / "app" / "data"
WIKI_API = "https://ko.wikisource.org/w/api.php"
GUTENBERG = "https://gutenberg.pglaf.org/cache/epub/{id}/pg{id}.txt"
GUTENBERG_BOILERPLATE = 19_000  # 구텐베르크 머리말·라이선스 꼬리말(바이트)
UA = {"User-Agent": "haedok-catalog/1.0 (length survey)"}


async def wiki_lengths(client: httpx.AsyncClient, titles: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for i in range(0, len(titles), 50):
        batch = titles[i : i + 50]
        r = await client.get(
            WIKI_API,
            params={
                "action": "query", "prop": "info", "titles": "|".join(batch), "redirects": 1,
                "format": "json", "formatversion": 2,
            },
        )  # fmt: skip
        q = r.json()["query"]
        alias = {t: t for t in batch}
        for n in q.get("normalized", []) + q.get("redirects", []):
            for orig, cur in list(alias.items()):
                if cur == n["from"]:
                    alias[orig] = n["to"]
        sizes = {p["title"]: p.get("length", 0) for p in q["pages"] if not p.get("missing")}
        for orig, cur in alias.items():
            if sizes.get(cur):
                out[orig] = sizes[cur] // 3
    return out


async def gutenberg_length(client: httpx.AsyncClient, sem: asyncio.Semaphore, gid: str) -> int | None:
    async with sem:
        try:
            r = await client.get(GUTENBERG.format(id=gid), headers={"Range": "bytes=0-0"})
        except httpx.HTTPError:
            return None
        total = r.headers.get("content-range", "").rpartition("/")[2]
        return max(int(total) - GUTENBERG_BOILERPLATE, 2_000) if total.isdigit() else None


async def main() -> None:
    catalog = json.loads((DATA / "catalog.json").read_text())
    async with httpx.AsyncClient(timeout=20, headers=UA, follow_redirects=True) as client:
        wiki = await wiki_lengths(client, [b["id"] for b in catalog if b["source"] == "wikisource"])
        sem = asyncio.Semaphore(8)
        gids = [b["id"] for b in catalog if b["source"] == "gutendex"]
        sizes = await asyncio.gather(*(gutenberg_length(client, sem, g) for g in gids))
    out = {f"wikisource:{k}": v for k, v in wiki.items()}
    out |= {f"gutendex:{g}": s for g, s in zip(gids, sizes, strict=True) if s}
    (DATA / "lengths.json").write_text(json.dumps(out, ensure_ascii=False, sort_keys=True, indent=0) + "\n")
    print(f"wikisource {len(wiki)} · gutenberg {sum(1 for s in sizes if s)} / {len(gids)}")


if __name__ == "__main__":
    asyncio.run(main())
