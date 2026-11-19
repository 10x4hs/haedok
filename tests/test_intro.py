"""공개 페이지: 첫 화면(로그아웃) · /guide 사용법 · /docs 규칙 · /developers. /intro는 /guide로 옮겼다."""

import re
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.public_facts import CLIP_V, FACTS, mp4_seconds
from app.services import goals, pace, points, races
from tests.conftest import signup
from tests.test_api import upstream

PUBLIC = ("/guide", "/docs", "/developers")
CLIPS = ("search", "verify", "unseal", "ask", "community", "goal")  # 첫 화면 기능 순서 그대로
STATIC = Path(__file__).resolve().parent.parent / "app" / "static"


@pytest.mark.parametrize("path", PUBLIC)
async def test_public_pages_open_without_login(api, path):
    async with api(upstream) as c:
        r = await c.get(path)
    assert r.status_code == 200
    assert r.text.count("<h1") == 1
    assert "/static/public.css" in r.text and '<a class="pn-skip" href="#main">' in r.text


async def test_intro_moved_to_guide(api):
    async with api(upstream) as c:
        r = await c.get("/intro")
    assert r.status_code in (301, 308) and r.headers["location"] == "/guide"


async def test_landing_drops_the_demo_pill_and_keeps_every_cta(api):
    async with api(upstream) as c:
        html = (await c.get("/")).text
    for gone in ("demoOpen", "demoModal", "41초 영상으로 보기"):
        assert gone not in html
    assert "같은 책을<br>각자의 속도로<br><em>함께</em> 읽어요" in html
    assert 'href="/signup"' in html and "무료로 시작하기" in html
    assert 'href="/login"' in html and "이미 계정이 있어요" in html
    assert 'data-install="ios"' in html and 'id="iosSheet"' in html
    assert 'href="/static/haedok.apk"' in html and 'data-install="android"' in html
    assert 'href="/guide"' in html and 'href="/intro"' not in html


async def test_landing_clips_play_inline_with_posters_and_captions(api):
    async with api(upstream) as c:
        html = (await c.get("/")).text
        for name in CLIPS:
            assert f'poster="/static/clips/{name}.jpg?v={CLIP_V[name + ".jpg"]}"' in html
            assert f'src="/static/clips/{name}.mp4?v={CLIP_V[name + ".mp4"]}"' in html
            assert f'aria-describedby="clip-{name}-d"' in html
            assert (await c.get(f"/static/clips/{name}.mp4")).status_code == 200
    # 소리 없이, 보일 때만 — 저절로 재생(autoplay) 속성은 쓰지 않는다
    videos = re.findall(r"<video data-clip[^>]*>", html)  # 소개 영상(직접 재생)은 따로 검사한다
    assert len(videos) == len(CLIPS)
    assert all(
        "muted" in v and "playsinline" in v and 'preload="none"' in v and "autoplay" not in v for v in videos
    )


@pytest.mark.parametrize(
    ("path", "label"), [("/", None), ("/guide", "사용법"), ("/docs", "Docs"), ("/developers", "Developer")]
)
async def test_nav_marks_the_current_page(api, path, label):
    async with api(upstream) as c:
        html = (await c.get(path)).text
    nav = html[html.index('<header class="pn">') : html.index("</header>", html.index('<header class="pn">'))]
    current = re.findall(r'<a [^>]*aria-current="page"[^>]*>([^<]*)<', nav)
    for text in ("사용법", "Docs", "Developer", "로그인"):
        assert text in nav
    if label is None:  # 첫 화면은 로고가 지금 페이지
        assert 'class="pn-home" href="/" aria-label="해독 첫 화면" aria-current="page"' in nav
        assert current == [""]
    else:
        assert current == [label]


async def test_docs_renders_rules_from_the_code(api):
    async with api(upstream) as c:
        html = (await c.get("/docs")).text
    for key, amount in points.RULES.items():
        assert points.LABELS[key] in html
        assert f'<td class="num mono">+{amount}</td>' in html
    assert f"쪽마다 {pace.PAGE_DWELL_MS // 1000}초" in html
    assert f"{pace.DWELL_CAP_MULTIPLIER}배" in html
    assert f"정원 {races.MAX_MEMBERS}명" in html and f"초대 코드 {races.CODE_LEN}자리" in html
    assert "마일리지" in html


async def test_guide_uses_the_real_screens(api):
    async with api(upstream) as c:
        html = (await c.get("/guide")).text
    for shot in ("1-login", "2-home", "3-explore", "5-reader"):
        assert f"/static/intro/{shot}.jpg" in html
    for step in range(1, 8):
        assert f'id="s{step}"' in html
    for name in ("search", "verify", "ask", "community", "goal"):
        assert f"/static/clips/{name}.mp4?v=" in html


async def test_developers_links_the_repo_and_counts_from_disk(api):
    async with api(upstream) as c:
        html = (await c.get("/developers")).text
    assert "https://github.com/10x4hs/haedok" in html
    assert ".venv/bin/pytest -q" in html


async def test_signed_in_visitor_gets_open_app_instead_of_login(api):
    async with api(upstream) as c:
        await signup(c)
        html = (await c.get("/guide")).text
    nav = html[html.index('<header class="pn">') :]
    assert '<a class="pn-pill pn-login" href="/">앱 열기</a>' in nav
    assert 'name="robots"' not in html  # 공개 문서는 로그인해도 검색에 열어 둔다


async def test_swagger_moved_off_docs(api):
    async with api(upstream) as c:
        assert (await c.get("/api/docs")).status_code == 200
        assert "swagger" not in (await c.get("/docs")).text.lower()


@pytest.mark.parametrize("path", ["/login", "/signup", "/password/forgot"])
async def test_auth_pages_draw_the_cover_wall_from_local_covers(api, path):
    async with api(upstream) as c:
        html = (await c.get(path)).text
    assert '<link rel="stylesheet" href="/static/auth.css">' in html
    wall = html[html.index('<div class="aw">') : html.index('<i class="auth-glass">')]
    covers = re.findall(r'<img src="([^"]+)"', wall)
    assert covers and all(src.startswith("/static/covers/showcase/") for src in covers)
    assert 'class="auth-card"' in html  # 유리 카드는 그대로


async def test_landing_plays_the_41s_demo_inline(api):
    """41초 소개 영상은 버튼·팝업 없이 첫 화면에 바로 있고, 파일도 실제로 있다."""
    async with api(upstream) as c:
        html = (await c.get("/")).text
        assert 'id="demo"' in html and "/static/demo/haedok-demo.mp4?v=" in html and "<video controls" in html
        assert (await c.get("/static/demo/haedok-demo.mp4")).status_code == 200


async def test_landing_puts_the_features_in_todays_order(api):
    async with api(upstream) as c:
        html = (await c.get("/")).text
    ids = ['id="find"', 'id="verify"', 'id="unseal"', 'id="ask"', 'id="community"', 'id="goal"']
    at = [html.index(i) for i in ids]
    assert at == sorted(at)
    assert f"하루 {get_settings().ai_daily_requests}번까지" in html  # AI 하루 한도는 설정에서
    assert f"{goals.GOAL_MIN}쪽부터 {goals.GOAL_MAX}쪽까지" in html
    for q in ("AI가 결말을 말해 버리지 않나요?", "내 글이 AI 학습에 쓰이나요?"):
        assert q in html


async def test_docs_states_the_ai_and_community_rules_from_the_code(api):
    async with api(upstream) as c:
        html = (await c.get("/docs")).text
    assert f"하루 {get_settings().ai_daily_requests}번" in html
    assert f"사진 {FACTS['post_photos']}장까지" in html
    assert f"{goals.GOAL_MIN}~{goals.GOAL_MAX}쪽" in html
    assert 'id="ai"' in html and 'id="community"' in html


async def test_developers_explains_the_ai_layer(api):
    async with api(upstream) as c:
        html = (await c.get("/developers")).text
    assert "pgvector" in html and "AI_PROVIDER=fake" in html
    assert f"테스트 함수 {FACTS['tests']}개" in html


def test_demo_length_on_the_page_comes_from_the_file():
    seconds = mp4_seconds(STATIC / "demo" / "haedok-demo.mp4")
    assert seconds is not None and 30 <= seconds <= 50
    assert FACTS["demo_s"] == seconds


@pytest.mark.parametrize("path", [*(f"clips/{n}.mp4" for n in CLIPS), "demo/haedok-demo.mp4"])
def test_public_videos_have_no_sound_track(path):
    """소개 영상은 소리 없이 — 음악 저작권 문제가 생기지 않게 오디오 트랙 자체가 없다."""
    data = (STATIC / path).read_bytes()
    assert b"hdlr" in data and b"soun" not in data
