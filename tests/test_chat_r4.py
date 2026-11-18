"""채팅 4차 — 읽기 게시물 링크가 볼 수 있는 사람에게만 카드로, 대화 소개 · 입력칸 구조."""

from app.core.config import get_settings
from app.models_posts import ReadingPost
from tests.test_chat import befriend, login_as, post, trio, uid  # noqa: F401


async def _post(session_factory, author_id: int, visibility: str = "all", title: str = "운수 좋은 날") -> int:
    async with session_factory() as s:
        p = ReadingPost(
            author_id=author_id,
            book_source="store",
            external_id=f"x-{visibility}-{title}",
            book_title=title,
            status="done",
            caption="좋았어요",
            visibility=visibility,
            post_day="2026-11-18",
            cover_url="javascript:alert(1)",
        )
        s.add(p)
        await s.commit()
        return p.id


async def test_post_link_becomes_card_only_for_viewers_who_may_see_it(trio, session_factory, api):  # noqa: F811
    h, m = trio["h"], trio["m"]
    h_id, m_id = trio["ids"][0], trio["ids"][1]
    url = f"/api/v1/chat/room/{trio['room']}/messages"
    public = await _post(session_factory, h_id, "all", "공개 기록")
    private = await _post(session_factory, h_id, "friends", "친구 공개 기록")
    await post(h, url, text=f"이거 봐요 https://haedok.app/p/{public} 그리고 /p/{private}", channel="chat")

    item = (await m.get(url, params={"channel": "chat"})).json()["items"][0]
    titles = [c["title"] for c in item.get("cards", [])]
    assert titles == ["공개 기록"]  # 민준은 하린의 친구가 아니라 '친구 공개' 기록은 카드가 되지 않는다
    assert item["cards"][0]["author"] == "하린" and item["cards"][0]["href"] == f"/p/{public}"
    assert item["cards"][0]["cover"] is None  # 안전하지 않은 표지 주소는 버린다
    assert "친구 공개 기록" not in (await m.get(url, params={"channel": "chat"})).text

    # 친구가 되면 친구 공개 기록도 카드로 보인다
    await befriend(h, m, m_id, h_id)
    titles = [c["title"] for c in (await m.get(url, params={"channel": "chat"})).json()["items"][0]["cards"]]
    assert titles == ["공개 기록", "친구 공개 기록"]


async def test_sealed_message_link_is_not_expanded(trio, session_factory):  # noqa: F811
    h, m = trio["h"], trio["m"]
    pid = await _post(session_factory, trio["ids"][0], "all", "결말 감상")
    url = f"/api/v1/chat/room/{trio['room']}/messages"
    await post(h, url, text=f"결말 감상 /p/{pid}", channel="book")
    r = await m.get(url, params={"channel": "book"})
    assert "결말 감상" not in r.text and "cards" not in r.json()["items"][0]


async def test_cards_off_when_posts_feature_off(trio, session_factory, monkeypatch):  # noqa: F811
    pid = await _post(session_factory, trio["ids"][0])
    url = f"/api/v1/chat/room/{trio['room']}/messages"
    await post(trio["h"], url, text=f"/p/{pid}", channel="chat")
    monkeypatch.setattr(get_settings(), "feature_posts", False)
    item = (await trio["m"].get(url, params={"channel": "chat"})).json()["items"][0]
    assert "cards" not in item and item["body"] == f"/p/{pid}"


async def test_thread_has_intro_and_quiet_composer(api, session_factory):
    a = await login_as(api, "a@example.com", "가")
    b = await login_as(api, "b@example.com", "나")
    a_id, b_id = await uid(session_factory, "a@example.com"), await uid(session_factory, "b@example.com")
    await befriend(a, b, b_id, a_id)
    html = (await a.get(f"/chat/dm/{b_id}")).text
    assert (
        'id="intro"' in html and f'href="/u/{b_id}"' in html
    )  # 소개의 '프로필 보기'(게시물 기능이 켜져 있을 때)
    assert 'placeholder="메시지 입력…"' in html and 'id="emojiBtn"' in html
    pill = html.split('class="ch-pill"')[1].split("</div>")[0]
    assert "helpBtn" not in pill  # 쓰는 법은 입력칸 밖(머리의 ⋯ 메뉴)
    for c in (a, b):
        await c.aclose()


async def test_shared_card_shows_the_first_photo_when_the_post_has_photos(trio, session_factory):  # noqa: F811
    """사진을 올린 게시물을 채팅에 붙이면 카드에 책 표지 대신 첫 사진이 나온다."""
    from app.models_posts import PostPhoto

    h, m = trio["h"], trio["m"]
    pid = await _post(session_factory, trio["ids"][0], "all", "사진 기록")
    async with session_factory() as s:
        s.add(PostPhoto(post_id=pid, idx=0, data=b"\xff\xd8\xff\xd9", width=1080, height=1350, digest="abcd1234abcd1234"))
        await s.commit()
    url = f"/api/v1/chat/room/{trio['room']}/messages"
    await post(h, url, text=f"/p/{pid}", channel="chat")
    card = (await m.get(url, params={"channel": "chat"})).json()["items"][0]["cards"][0]
    assert card["cover"] == f"/media/p/{pid}/0.jpg?v=abcd1234abcd1234"
