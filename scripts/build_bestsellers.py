"""Yes24 분야별 베스트(소설·인문·에세이)를 읽어 app/data/bestsellers.json에 적는다.

하루 한 번 GitHub Actions(.github/workflows/bestsellers.yml)가 돌린다.

실행: python scripts/build_bestsellers.py
한 분야라도 못 읽으면(화면 구조가 바뀌었거나 접속 실패) 파일을 건드리지 않고 0이 아닌 값으로 끝난다.
"""

import datetime as dt
import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import bestsellers  # noqa: E402

UA = {"User-Agent": "haedok/0.1 (+https://github.com/10x4hs/haedok; daily bestseller refresh)"}


def main() -> int:
    books: list[dict] = []
    with httpx.Client(headers=UA, timeout=20, follow_redirects=True) as client:
        for n, (name, number) in enumerate(bestsellers.CATEGORIES):
            if n:
                time.sleep(2)  # 예의 있게 — 하루에 세 번, 사이를 띄운다
            try:
                resp = client.get(bestsellers.URL, params=bestsellers.params(number))
                resp.raise_for_status()
            except httpx.HTTPError as e:
                print(f"{name}: fetch failed: {type(e).__name__}: {e}", file=sys.stderr)
                return 1
            found = bestsellers.parse_yes24(resp.text, limit=bestsellers.PER_CATEGORY)
            if not found:
                print(f"{name}: nothing parsed — leaving the file as it is", file=sys.stderr)
                return 1
            books += [b | {"category": name} for b in found]
    old = bestsellers.load()
    if [(b.get("category"), b["id"]) for b in old] == [(b["category"], b["id"]) for b in books]:
        print("no change")
        return 0
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).date().isoformat()
    payload = {"source": "Yes24 분야별 베스트 (소설·인문·에세이)", "updated": today, "books": books}
    bestsellers.DATA.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("updated:", ", ".join(f"{b['category']} 1위 {b['title']}" for b in bestsellers.leaders(books)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
