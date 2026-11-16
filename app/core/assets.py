"""정적 파일 캐시 — 파일 내용 해시를 주소에 붙여(`?v=`) 1년 캐시하고, 바뀌면 주소가 바뀌게 한다.

- 템플릿의 `href="/static/….css"`, `src="/static/….js"`는 컴파일 전에 자동으로
  `…?v={{ static_v('/static/….css') }}`로 바뀐다(StaticVersionExtension). 템플릿을 손대지 않아도 되고,
  새로 추가하는 CSS·JS 링크도 저절로 따라온다.
- 버전은 파일마다의 내용 해시(앞 10자리)다. (경로, mtime, 크기)로 캐시해 요청마다 파일을 읽지 않는다.
  배포마다 바뀌는 커밋 SHA와 달리, 안 바뀐 파일은 배포를 넘어서도 브라우저 캐시가 그대로 맞는다.
- /static 응답: `?v=`가 지금 내용의 해시와 같을 때만 `immutable` 1년, 그 밖(버전 없음·옛 버전·엉뚱한 값)은
  짧은 캐시. 옛 주소로 새 내용을 받아 1년 동안 붙드는 일이 없다.
"""

import hashlib
import os
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qs

from jinja2 import Environment
from jinja2.ext import Extension
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

STATIC_DIR = (Path(__file__).resolve().parents[1] / "static").resolve()
STATIC_PREFIX = "/static/"

IMMUTABLE = "public, max-age=31536000, immutable"
SHORT = "public, max-age=300"

# 속성 값 전체가 /static/…(.css|.js)인 경우만 — Jinja 식 안이나 이미 ?가 붙은 주소는 건드리지 않는다
_STATIC_ATTR = re.compile(
    r"""(?P<lead>\b(?:href|src)=(?P<q>["']))(?P<url>/static/[^"'?#{}<>\s]+\.(?:css|js))(?P=q)"""
)


def _file_of(url_path: str) -> Path | None:
    """`/static/…` 주소를 static 폴더 안의 실제 파일로. 폴더 밖을 가리키면 None."""
    if not url_path.startswith(STATIC_PREFIX):
        return None
    candidate = (STATIC_DIR / url_path.removeprefix(STATIC_PREFIX)).resolve()
    if not candidate.is_relative_to(STATIC_DIR) or not candidate.is_file():
        return None
    return candidate


@lru_cache(maxsize=256)
def _digest(path: str, mtime_ns: int, size: int) -> str:
    del mtime_ns, size  # 캐시 키로만 쓴다 — 파일이 바뀌면 새로 계산된다
    return hashlib.sha1(Path(path).read_bytes()).hexdigest()[:10]


def version_of(url_path: str) -> str:
    """정적 파일의 지금 버전(내용 해시 10자리). 없는 파일이면 빈 문자열."""
    f = _file_of(url_path)
    if f is None:
        return ""
    st = f.stat()
    return _digest(str(f), st.st_mtime_ns, st.st_size)


def static_v(url_path: str) -> str:
    """Jinja 전역 — `?v=` 뒤에 붙일 값."""
    return version_of(url_path)


def add_versions(source: str) -> str:
    def repl(m: re.Match[str]) -> str:
        url = m["url"]
        return f"{m['lead']}{url}?v={{{{ static_v('{url}') }}}}{m['q']}"

    return _STATIC_ATTR.sub(repl, source)


class StaticVersionExtension(Extension):
    """템플릿을 컴파일하기 전에 정적 CSS·JS 주소에 버전을 붙인다."""

    def preprocess(self, source: str, name: str | None, filename: str | None = None) -> str:
        return add_versions(source)


def install(env: Environment) -> None:
    env.add_extension(StaticVersionExtension)
    env.globals["static_v"] = static_v


def cache_control(url_path: str, query_string: bytes) -> str:
    given = parse_qs(query_string.decode("latin-1")).get("v")
    if given and len(given) == 1 and given[0] and given[0] == version_of(url_path):
        return IMMUTABLE
    return SHORT


class CachedStaticFiles(StaticFiles):
    """StaticFiles + Cache-Control. 성공(200·304)에만 단다 — 404를 캐시하게 하지 않는다."""

    async def get_response(self, path: str, scope: Scope):
        response = await super().get_response(path, scope)
        if response.status_code in (200, 206, 304):
            url_path = STATIC_PREFIX + path.replace(os.sep, "/").lstrip("/")
            response.headers["Cache-Control"] = cache_control(url_path, scope.get("query_string", b""))
        return response


def build_stamp() -> str:
    """배포 도장 — 서비스 워커 캐시 이름에 쓴다.

    Vercel은 배포마다 커밋 SHA를 주므로 그걸 쓰고, 로컬·도커는 정적 CSS·JS(하위 폴더 포함) 내용의 해시를 쓴다.
    """
    sha = os.environ.get("VERCEL_GIT_COMMIT_SHA", "")
    if sha:
        return sha[:10]
    h = hashlib.sha1()
    code = sorted(STATIC_DIR.rglob("*.css")) + sorted(STATIC_DIR.rglob("*.js"))
    for f in [*code, STATIC_DIR / "offline.html"]:
        if f.is_file():
            h.update(f.relative_to(STATIC_DIR).as_posix().encode())
            h.update(f.read_bytes())
    return h.hexdigest()[:10]
