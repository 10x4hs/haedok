"""홈 3D 띠에 거는 '유명하고, 앱에서 바로 무료로 읽을 수 있는' 책 목록(app/data/showcase.json)을 만든다.

실행: .venv/bin/python scripts/build_showcase.py
- 아래 BOOKS는 손으로 고른 목록이다. 한국 작품은 저자가 1962년 이전에 세상을 떠나 국내 퍼블릭 도메인,
  세계 고전은 미국 퍼블릭 도메인(Project Gutenberg)이다. 1984·동물농장·노인과 바다처럼
  미국에서 아직 보호되는 책은 넣지 않는다.
- 앱과 같은 소스 클래스로 본문을 실제로 받아 와서 (1) 본문이 비어 있지 않은지(목차·라이선스 틀만 있는
  위키문헌 문서는 걸러 낸다) 확인하고 (2) 읽는 시간(분)을 잰다.
- 표지는 scripts/build_covers.py가 만든다(직접 만든 타이포그래피 표지, 출판사 표지 아님).
"""

import asyncio
import json
from pathlib import Path

import httpx

from app.sources.gutendex import GutendexSource
from app.sources.wikisource import WikisourceSource

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "app" / "data" / "showcase.json"
MIN_CHARS = 300  # 이보다 짧으면 본문이 아니라 틀(라이선스 안내)뿐인 문서다
KO_CHARS_PER_MIN = 500
EN_WORDS_PER_MIN = 200

# slug, source, id, 제목, 저자, 해, 묶음, 원제
BOOKS = [
    ("seosi", "wikisource", "하늘과 바람과 별과 시 (1948년)/서시", "서시", "윤동주", 1941, "ko", None),
    ("lucky-day", "wikisource", "운수 좋은 날", "운수 좋은 날", "현진건", 1924, "ko", None),
    ("buckwheat", "wikisource", "메밀꽃 필 무렵", "메밀꽃 필 무렵", "이효석", 1936, "ko", None),
    ("wings", "wikisource", "날개", "날개", "이상", 1936, "ko", None),
    ("camellia", "wikisource", "동백꽃", "동백꽃", "김유정", 1936, "ko", None),
    ("spring", "wikisource", "봄봄", "봄봄", "김유정", 1935, "ko", None),
    ("potato", "wikisource", "감자", "감자", "김동인", 1925, "ko", None),
    ("ready-made", "wikisource", "레디메이드 인생", "레디메이드 인생", "채만식", 1934, "ko", None),
    ("b-sagam", "wikisource", "B사감과 러브레터", "B사감과 러브레터", "현진건", 1925, "ko", None),
    ("samryong", "wikisource", "벙어리 삼룡이", "벙어리 삼룡이", "나도향", 1925, "ko", None),
    ("hong-gildong", "wikisource", "홍길동전 (30장 경판본)", "홍길동전", "작자 미상", None, "ko", None),
    ("gatsby", "gutendex", "64317", "위대한 개츠비", "F. 스콧 피츠제럴드", 1925, "world", "The Great Gatsby"),
    ("metamorphosis", "gutendex", "5200", "변신", "프란츠 카프카", 1915, "world", "Metamorphosis"),
    ("pride", "gutendex", "1342", "오만과 편견", "제인 오스틴", 1813, "world", "Pride and Prejudice"),
    ("sherlock", "gutendex", "1661", "셜록 홈즈의 모험", "아서 코난 도일", 1892, "world",
     "The Adventures of Sherlock Holmes"),
    ("alice", "gutendex", "11", "이상한 나라의 앨리스", "루이스 캐럴", 1865, "world",
     "Alice's Adventures in Wonderland"),
    ("crime", "gutendex", "2554", "죄와 벌", "표도르 도스토옙스키", 1866, "world", "Crime and Punishment"),
    ("frankenstein", "gutendex", "84", "프랑켄슈타인", "메리 셸리", 1818, "world", "Frankenstein"),
    ("siddhartha", "gutendex", "2500", "싯다르타", "헤르만 헤세", 1922, "world", "Siddhartha"),
    ("dorian", "gutendex", "174", "도리언 그레이의 초상", "오스카 와일드", 1890, "world",
     "The Picture of Dorian Gray"),
]  # fmt: skip


def minutes(text: str, lang: str) -> int:
    if lang == "ko":
        return max(1, round(len(text) / KO_CHARS_PER_MIN))
    return max(1, round(len(text.split()) / EN_WORDS_PER_MIN))


async def main() -> None:
    out = []
    async with httpx.AsyncClient(
        timeout=60, follow_redirects=True, headers={"User-Agent": "haedok-build/1.0"}
    ) as client:
        sources = {"wikisource": WikisourceSource(client), "gutendex": GutendexSource(client)}
        for slug, source, ext_id, title, author, year, group, original in BOOKS:
            for attempt in range(4):
                try:
                    _, text = await sources[source].fetch_book(ext_id)
                    break
                except httpx.HTTPStatusError as e:  # 위키문헌은 빨리 부르면 429, 구텐베르크는 가끔 503
                    if (e.response.status_code != 429 and e.response.status_code < 500) or attempt == 3:
                        raise
                    await asyncio.sleep(10 * (attempt + 1))
            if len(text) < MIN_CHARS:
                print(f"skip {title}: body is only {len(text)} chars")
                continue
            lang = "ko" if source == "wikisource" else "en"
            item = {
                "source": source,
                "id": ext_id,
                "title": title,
                "author": author,
                "year": year,
                "group": group,
                "lang": lang,
                "minutes": minutes(text, lang),
                "cover": f"/static/covers/home/{slug}.webp",
            }
            if original:
                item["original"] = original
            out.append(item)
            print(f"{title:20} {item['minutes']:5}분")
            await asyncio.sleep(2)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    print(f"wrote {len(out)} books → {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    asyncio.run(main())
