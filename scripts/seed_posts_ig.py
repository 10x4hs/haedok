"""라운드 5 스크린샷용 데이터 — seed_posts.py 뒤에 실행한다.

    python scripts/seed_posts_ig.py "$DATABASE_URL" <사진 폴더>

사진 폴더의 JPEG(desk · page · shelf · cafe · bed · park .jpg)를 게시물에 붙인다 — 서버와 똑같이
post_photos.check_upload 로 검사하고 EXIF 를 걷어 낸 바이트만 넣는다.
하린 '데미안'은 사진 세 장(넘김), 하린 '이방인'(친구만) · '노인과 바다'(정사각) · 서연 '페스트'는 한 장씩.
댓글 답글 · 댓글 좋아요 · 저장 · 소개 한 줄도 넣는다.
"""

import asyncio
import datetime as dt
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

PHOTOS = {
    ("하린", "데미안"): ["desk", "page", "shelf"],
    ("하린", "이방인"): ["cafe"],
    ("하린", "노인과 바다"): ["park"],
    ("서연", "페스트"): ["bed"],
}
BIOS = {
    "하린": "고전을 천천히, 소리 내어 읽어요 📚\n이번 달은 헤세 읽는 중",
    "민준": "디스토피아 소설 모으는 사람",
}


async def main(db_url: str, folder: Path) -> None:
    from app.models import User
    from app.models_posts import PostComment, PostCommentLike, PostPhoto, PostSave, ProfileBio, ReadingPost
    from app.services import post_photos as pp

    engine = create_async_engine(db_url)
    now = dt.datetime.now(dt.UTC)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        users = {u.nickname: u for u in await s.scalars(select(User))}
        nick = {u.id: u.nickname for u in users.values()}
        posts = {(nick[p.author_id], p.book_title): p for p in await s.scalars(select(ReadingPost))}
        for key, names in PHOTOS.items():
            post = posts[key]
            for i, name in enumerate(names):
                clean = pp.check_upload((folder / f"{name}.jpg").read_bytes())
                s.add(
                    PostPhoto(
                        post_id=post.id,
                        idx=i,
                        data=clean.data,
                        width=clean.width,
                        height=clean.height,
                        digest=clean.digest,
                        created_at=post.created_at,
                    )
                )
        demian = posts[("하린", "데미안")]
        first = await s.scalar(
            select(PostComment).where(PostComment.post_id == demian.id).order_by(PostComment.id)
        )
        for i, (who, body) in enumerate(
            [
                ("하린", "@민준 저도요! 다시 읽으니 완전히 다른 책 같아요."),
                ("서연", "@하린 이번엔 같이 읽어요"),
            ]
        ):
            s.add(
                PostComment(
                    post_id=demian.id,
                    user_id=users[who].id,
                    body=body,
                    parent_id=first.id,
                    created_at=now - dt.timedelta(minutes=50 - i * 20),
                )
            )
        for who in ("하린", "서연"):
            s.add(PostCommentLike(comment_id=first.id, user_id=users[who].id))
        for title_key in (("민준", "1984"), ("서연", "페스트"), ("도윤", "수레바퀴 아래서")):
            s.add(PostSave(post_id=posts[title_key].id, user_id=users["하린"].id, created_at=now))
        for who, bio in BIOS.items():
            s.add(ProfileBio(user_id=users[who].id, bio=bio, updated_at=now))
        await s.commit()
    await engine.dispose()
    print("seeded photos for", len(PHOTOS), "posts")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], Path(sys.argv[2])))
