"""공개 페이지(/, /guide, /docs, /developers)에 쓰는 숫자 — 손으로 적지 않고 코드의 상수에서 읽는다.

규칙이 바뀌면 문서도 같이 바뀐다. 테스트·마이그레이션·JS 수는 import 때 센다(배포 번들에 tests/나
migrations/가 없으면 None — 화면은 그 줄을 뺀다).
"""

import hashlib
import re
from pathlib import Path

from app import auth
from app.core.config import get_settings
from app.services import (
    chat,
    goals,
    pace,
    points,
    post_photos,
    posts,
    races,
    rag,
    reader_ai,
    social,
    uploads,
    vectors,
)

ROOT = Path(__file__).resolve().parent.parent

REPO = "https://github.com/10x4hs/haedok"

# 마일리지(코드 이름은 points) 규칙마다 '언제 받는지'.
# 금액은 points.RULES에서 읽는다(여기에 숫자를 적지 않는다)
POINT_WHEN = {
    points.CHUNK: "서버가 '다 읽음'으로 인정한 쪽마다 받아요. 같은 책의 같은 쪽은 한 번만이에요.",
    points.REACTION: f"문단에 처음 남긴 반응마다 받아요. 하루 {points.REACTION_DAILY_CAP}번까지예요.",
    points.RACE_FINISH: "대결방의 책을 끝까지 읽으면 받아요. 방마다 한 번이에요.",
    points.OVERTAKE: "같은 방 친구를 앞지르면 받아요. 친구마다 한 번이에요.",
    points.QUOTE: "대결방에 마음에 든 문장을 공유하면 받아요.",
    points.STREAK: f"{points.STREAK_DAYS}일 동안 하루도 빠짐없이 읽으면 받아요. "
    f"{points.STREAK_DAYS}일, {points.STREAK_DAYS * 2}일, {points.STREAK_DAYS * 3}일…마다 다시 받아요.",
}


def _count_tests() -> int | None:
    folder = ROOT / "tests"
    if not folder.is_dir():
        return None  # 배포 번들에는 tests/가 없다 — 그때는 숫자를 빼고 보여 준다
    pattern = re.compile(r"^\s*(?:async\s+)?def test_", re.MULTILINE)
    return sum(len(pattern.findall(p.read_text(encoding="utf-8"))) for p in folder.rglob("test_*.py"))


def _count_migrations() -> int | None:
    folder = ROOT / "migrations" / "versions"
    if not folder.is_dir():
        return None
    return sum(1 for p in folder.glob("*.py") if not p.name.startswith("__"))


def _count_js() -> tuple[int, int]:
    files = sorted((ROOT / "app" / "static").glob("*.js"))
    return len(files), sum(len(p.read_text(encoding="utf-8").splitlines()) for p in files)


def clip_versions(sub: str = "clips") -> dict[str, str]:
    """영상 파일마다 내용 지문(8자리). 파일 이름이 같아도 바뀌면 주소가 바뀌어 캐시를 새로 받는다."""
    folder = ROOT / "app" / "static" / sub
    return {p.name: hashlib.sha1(p.read_bytes()).hexdigest()[:8] for p in sorted(folder.glob("*.*"))}


def mp4_seconds(path: Path) -> int | None:
    """MP4 의 mvhd 상자에서 길이(초, 반올림)를 읽는다 — 화면의 'n초로 먼저 보기'가 실제 영상과 같게."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    i = data.find(b"mvhd")
    if i < 0:
        return None
    num = lambda a, b: int.from_bytes(data[i + a : i + b], "big")  # noqa: E731
    scale, length = (num(24, 28), num(28, 36)) if data[i + 4] == 1 else (num(16, 20), num(20, 24))
    return round(length / scale) if scale else None


CLIP_V = clip_versions()
DEMO_V = clip_versions("demo")  # 첫 화면 소개 영상
DEMO_S = mp4_seconds(ROOT / "app" / "static" / "demo" / "haedok-demo.mp4")


def _facts() -> dict:
    s = get_settings()

    def secs(ms: int) -> str:  # 화면에 보이는 초 — DWELL_SCALE(시연 배율)을 곱한 값
        return f"{ms * s.dwell_scale / 1000:g}"

    page_min_s, page_max_s = secs(pace.MIN_PAGE_MS), secs(pace.MAX_PAGE_MS)
    js_files, js_lines = _count_js()
    return {
        "page_min_s": page_min_s,
        "page_max_s": page_max_s,
        "page_range": f"{page_min_s}~{page_max_s}초",
        "page_rule": f"쪽의 글자 수에 맞춰 {page_min_s}~{page_max_s}초",
        "base_min_s": pace.MIN_PAGE_MS // 1000,
        "base_max_s": pace.MAX_PAGE_MS // 1000,
        "chars_per_min": pace.CJK_CHARS_PER_MIN,
        "words_per_min": pace.WORDS_PER_MIN,
        "cap_x": pace.DWELL_CAP_MULTIPLIER,
        "goal_range": f"{goals.GOAL_MIN}~{goals.GOAL_MAX}쪽",
        "goal_min": goals.GOAL_MIN,
        "goal_max": goals.GOAL_MAX,
        "freeze_cap": goals.FREEZE_CAP,
        "cap_max_s": secs(pace.MAX_PAGE_MS * pace.DWELL_CAP_MULTIPLIER),
        "points": [
            {"key": k, "label": points.LABELS[k], "amount": v, "when": POINT_WHEN.get(k, "")}
            for k, v in points.RULES.items()
        ],
        "reaction_cap": points.REACTION_DAILY_CAP,
        "emojis": social.EMOJIS,
        "max_comment": social.MAX_COMMENT,
        "code_len": races.CODE_LEN,
        "max_members": races.MAX_MEMBERS,
        "durations": races.DURATIONS,
        "formats": [f.lstrip(".").upper() for f in uploads.ALLOWED],
        "max_upload_mb": s.max_upload_mb,
        "max_unzipped_mb": uploads.MAX_UNZIPPED_BYTES // (1024 * 1024),
        "max_pdf_pages": uploads.MAX_PDF_PAGES,
        "code_digits": len(auth.new_code()),
        "code_min": auth.CODE_TTL_S // 60,
        "code_attempts": auth.MAX_CODE_ATTEMPTS,
        "resend_s": auth.RESEND_COOLDOWN_S,
        "min_password": auth.MIN_PASSWORD_CHARS,
        "remember_days": auth.REMEMBER_DAYS,
        "ai_daily": s.ai_daily_requests,
        "ai_keep": rag.KEEP,
        "ai_question": rag.MAX_QUESTION,
        "ai_top_k": rag.TOP_K,
        "ai_part_chars": vectors.PART_CHARS,
        "ai_actions": list(reader_ai.ACTIONS.values()),
        "post_photos": post_photos.MAX_PHOTOS,
        "photo_daily": post_photos.DAILY_PHOTOS,
        "post_visibility": list(posts.VISIBILITY.values()),
        "max_message": chat.MAX_BODY,
        "demo_s": DEMO_S,
        "tests": _count_tests(),
        "migrations": _count_migrations(),
        "js_files": js_files,
        "js_lines": js_lines,
        "repo": REPO,
    }


FACTS = _facts()
