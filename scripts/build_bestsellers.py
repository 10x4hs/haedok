"""Yes24 종합 베스트를 읽어 app/data/bestsellers.json에 적는다 (하루 한 번, GitHub Actions).

실행: python scripts/build_bestsellers.py
세 권도 못 읽으면(화면 구조가 바뀌었거나 접속 실패) 파일을 건드리지 않고 0이 아닌 값으로 끝난다.
"""

import datetime as dt
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import bestsellers  # noqa: E402

UA = {"User-Agent": "haedok/0.1 (+https://github.com/10x4hs/haedok; daily bestseller refresh)"}


def main() -> int:
    try:
        resp = httpx.get(
            bestsellers.URL, params=bestsellers.PARAMS, headers=UA, timeout=20, follow_redirects=True
        )
        resp.raise_for_status()
    except httpx.HTTPError as e:
        print(f"fetch failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    books = bestsellers.parse_yes24(resp.text, limit=10)
    if len(books) < 3:
        print(f"only {len(books)} books parsed — leaving the file as it is", file=sys.stderr)
        return 1
    old = bestsellers.load()
    if [b["id"] for b in old] == [b["id"] for b in books]:
        print("no change")
        return 0
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).date().isoformat()
    payload = {"source": "Yes24 종합 베스트", "updated": today, "books": books}
    bestsellers.DATA.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("updated:", ", ".join(b["title"] for b in books[:3]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
