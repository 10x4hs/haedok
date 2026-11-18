"""읽은 책 게시물 스크린샷용 데이터 — readme_shots.py --seed 와 seed_chat.py 뒤에 실행한다.

    python scripts/seed_posts.py "$DATABASE_URL"

하린(친구: 민준 · 서연)의 게시물 7개(친구만 1개, 표지 없는 고전 1개), 민준 3개, 서연 2개,
모르는 사람 도윤 4개(모두 공개). 하린의 한 게시물에 댓글(가리기 포함)과 좋아요, 도윤 게시물에 신고 두 건.
표지는 서점(알라딘) 표지 주소 — 화면에서 바깥 이미지를 불러온다.
"""

import asyncio
import datetime as dt
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

AL = "https://image.aladin.co.kr/product/"
BOOKS = {
    "데미안": ("헤르만 헤세", AL + "2062/85/cover500/e899721379_1.jpg"),
    "싯다르타": ("헤르만 헤세", AL + "3536/26/cover500/e893746058_1.jpg"),
    "동물농장": ("조지 오웰", AL + "23816/3/cover500/e422537194_1.jpg"),
    "1984": ("조지 오웰", AL + "1809/47/cover500/e893746077_1.jpg"),
    "어린 왕자": ("앙투안 드 생텍쥐페리", AL + "2245/67/cover500/e912435521_1.jpg"),
    "노인과 바다": ("어니스트 헤밍웨이", AL + "2754/40/cover500/e232435044_3.jpg"),
    "이방인": ("알베르 카뮈", AL + "1933/52/cover500/e032435820_1.jpg"),
    "페스트": ("알베르 카뮈", AL + "23483/56/cover500/ek79263858_1.jpg"),
    "운수 좋은 날": ("현진건", AL + "1522/59/cover500/8965880777_1.jpg"),
    "감자": ("김동인", AL + "53/3/cover500/8932015538_1.jpg"),
    "B사감과 러브레터": ("현진건", AL + "85/4/cover500/8936603655_1.jpg"),
    "배따라기": ("김동인", AL + "1522/60/cover500/8965880815_2.jpg"),
    "레디메이드 인생": ("채만식", AL + "53/3/cover500/8932015562_1.jpg"),
    "빈처": ("현진건", AL + "30/82/cover500/8908032495_1.jpg"),
    "님의 침묵": ("한용운", None),  # 표지 없는 고전 — 결정적 표지로 그린다
    "수레바퀴 아래서": ("헤르만 헤세", AL + "2778/71/cover500/e942435144_1.jpg"),
}

POSTS = [
    # (누가, 책, 상태, 별점, 글, 문장, 공개, 몇 시간 전)
    (
        "하린",
        "데미안",
        "done",
        5,
        "새는 알에서 나오려고 투쟁한다. 스무 살에 읽었을 때랑 지금이 이렇게 다를 줄이야.\n"
        "싱클레어가 ||데미안의 정체를 알게 되는 장면||에서 한참 멈춰 있었어요.",
        "새는 알에서 나오려고 투쟁한다. 알은 세계이다.",
        "all",
        2,
    ),
    (
        "하린",
        "어린 왕자",
        "done",
        4,
        "퇴근길 지하철에서 한 번에 다 읽었어요. 여우 이야기는 몇 번을 읽어도 좋네요.",
        "가장 중요한 것은 눈에 보이지 않아.",
        "all",
        26,
    ),
    ("하린", "이방인", "reading", None, "첫 문장부터 서늘해요. 아직 1부 중간쯤.", None, "friends", 50),
    (
        "하린",
        "님의 침묵",
        "done",
        5,
        "소리 내어 읽으니 더 좋았던 시집.",
        "님은 갔습니다. 아아 사랑하는 나의 님은 갔습니다.",
        "all",
        75,
    ),
    ("하린", "노인과 바다", "done", 4, "짧은데 오래 남는 책.", None, "all", 120),
    (
        "하린",
        "운수 좋은 날",
        "done",
        5,
        "제목이 이렇게 아플 수 있나요.",
        "설렁탕을 사다 놓았는데 왜 먹지를 못하니",
        "all",
        170,
    ),
    ("하린", "감자", "done", 3, "", None, "all", 230),
    (
        "민준",
        "1984",
        "done",
        5,
        "빅 브라더가 지켜보고 있다. 요즘 읽으니 더 무섭네요.",
        "전쟁은 평화, 자유는 예속, 무지는 힘.",
        "all",
        5,
    ),
    (
        "민준",
        "동물농장",
        "done",
        4,
        "짧고 날카로운 우화. 돼지들이 두 발로 걷는 장면에서 소름.",
        None,
        "friends",
        60,
    ),
    ("민준", "싯다르타", "reading", None, "천천히 읽는 중이에요.", None, "all", 140),
    ("서연", "페스트", "done", 4, "||리유의 마지막 독백||이 계속 생각나요. 같이 읽을 사람?", None, "all", 9),
    ("서연", "B사감과 러브레터", "done", 5, "웃기고 슬프고 짧아요.", None, "all", 100),
    (
        "도윤",
        "수레바퀴 아래서",
        "done",
        4,
        "학교 다닐 때 읽었으면 좋았을 책.",
        "지쳐 버리면 안 돼. 그러면 수레바퀴 아래 깔리게 돼.",
        "all",
        3,
    ),
    ("도윤", "배따라기", "done", 3, "바다 냄새 나는 단편.", None, "all", 30),
    ("도윤", "레디메이드 인생", "done", 4, "백 년 전 취준생 이야기인데 너무 지금 같아요.", None, "all", 80),
    ("도윤", "빈처", "reading", None, "아내 이야기에 마음이 쓰여요.", None, "all", 150),
]


async def main(db_url: str) -> None:
    from app import auth
    from app.models import Book, LibraryEntry, Shelf, User
    from app.models_posts import PostComment, PostLike, PostReport, ReadingPost
    from app.services.posts import KST

    engine = create_async_engine(db_url)
    now = dt.datetime.now(dt.UTC)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        users = {u.nickname: u for u in await s.scalars(select(User))}
        if "도윤" not in users:
            u = User(
                nickname="도윤", email="doyun@example.com", password_hash=auth.hash_password("password123")
            )
            s.add(u)
            await s.flush()
            users["도윤"] = u
        # 하린의 서재: 다 읽은 책 · 읽는 중(올리기 화면의 '내 서재' 줄)
        for i, title in enumerate(
            ("데미안", "어린 왕자", "님의 침묵", "운수 좋은 날", "노인과 바다", "이방인", "1984")
        ):
            author, cover = BOOKS[title]
            b = Book(
                source="store", external_id=f"seed-posts-{i}", title=title, author=author, cover_url=cover
            )
            s.add(b)
            await s.flush()
            s.add(
                LibraryEntry(
                    user_id=users["하린"].id,
                    book_id=b.id,
                    shelf=Shelf.READING if title in ("이방인", "1984") else Shelf.DONE,
                )
            )
        posts = {}
        for who, title, status, rating, caption, quote, vis, hours in sorted(POSTS, key=lambda p: -p[7]):
            author, cover = BOOKS[title]
            at = now - dt.timedelta(hours=hours)
            p = ReadingPost(
                author_id=users[who].id,
                book_source="seed",
                external_id=title,
                book_title=title,
                book_author=author,
                cover_url=cover,
                status=status,
                rating=rating,
                caption=caption,
                quote=quote,
                visibility=vis,
                post_day=at.astimezone(KST).date().isoformat(),
                created_at=at,
            )
            s.add(p)
            await s.flush()
            posts[(who, title)] = p
        demian = posts[("하린", "데미안")]
        for i, (who, body) in enumerate(
            [
                ("민준", "저도 이 문장 때문에 다시 읽었어요."),
                ("서연", "||마지막 장면|| 얘기 나중에 같이 해요 🙂"),
                ("하린", "좋아요! 이번 주말에 대결방 열어 볼게요."),
            ]
        ):
            s.add(
                PostComment(
                    post_id=demian.id,
                    user_id=users[who].id,
                    body=body,
                    created_at=now - dt.timedelta(minutes=90 - i * 30),
                )
            )
        for who in ("민준", "서연", "도윤"):
            s.add(PostLike(post_id=demian.id, user_id=users[who].id))
        for who in ("민준", "하린"):
            s.add(PostLike(post_id=posts[("서연", "페스트")].id, user_id=users[who].id))
        s.add(PostLike(post_id=posts[("민준", "1984")].id, user_id=users["하린"].id))
        target = posts[("도윤", "배따라기")]
        s.add(PostReport(post_id=target.id, reporter_id=users["민준"].id, reason="spam"))
        s.add(PostReport(post_id=target.id, reporter_id=users["서연"].id, reason="spoiler"))
        await s.commit()
    await engine.dispose()
    print("seeded posts:", len(POSTS))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
