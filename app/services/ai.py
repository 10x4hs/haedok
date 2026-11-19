"""AI 호출 한곳 — 대화(chat)와 임베딩(embed).

특정 회사 SDK 에 묶이지 않도록 OpenAI 호환 HTTP API 를 httpx 로 직접 부른다(새 의존성 없음).
값싼 모델이면 무엇이든 주소·키·모델 이름만 설정하면 된다(app/core/config.py 의 ai_* 참고).

AI_PROVIDER=fake 는 키 없이 결정적으로 답하는 가짜다 — 테스트와 로컬 미리보기에서 쓴다.
모든 기능은 settings.ai_enabled 가 False 면 입구를 숨기고, 여기 함수는 AIUnavailable 을 던진다.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
from dataclasses import dataclass

import httpx

from app.core.config import get_settings


class AIUnavailable(RuntimeError):
    """AI 를 쓸 수 없다(설정 없음 · 공급자 오류 · 시간 초과). 화면은 조용히 대체 문구를 보여 준다."""


@dataclass
class ChatResult:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0


def enabled() -> bool:
    return get_settings().ai_enabled


async def chat(
    messages: list[dict],
    *,
    system: str | None = None,
    max_tokens: int = 700,
    json_mode: bool = False,
    http: httpx.AsyncClient | None = None,
) -> ChatResult:
    """messages = [{"role": "user"|"assistant", "content": str}, …]. 답 한 개를 돌려준다."""
    s = get_settings()
    if not s.ai_enabled:
        raise AIUnavailable("AI 가 꺼져 있어요")
    if s.ai_provider == "fake":
        return _fake_chat(messages, system)
    body: dict = {
        "model": s.ai_chat_model,
        "messages": ([{"role": "system", "content": system}] if system else []) + messages,
        "max_tokens": max_tokens,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    data = await _post(s.ai_base_url, s.ai_api_key, "/chat/completions", body, http)
    try:
        text = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as e:
        raise AIUnavailable("AI 응답을 읽지 못했어요") from e
    usage = data.get("usage") or {}
    return ChatResult(text.strip(), data.get("model", s.ai_chat_model),
                      int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0))


async def embed(texts: list[str], *, http: httpx.AsyncClient | None = None) -> list[list[float]]:
    """문장마다 길이 ai_embed_dim 의 단위 벡터. 순서는 입력과 같다."""
    s = get_settings()
    if not s.ai_enabled:
        raise AIUnavailable("AI 가 꺼져 있어요")
    if not texts:
        return []
    if s.ai_provider == "fake":
        return [_fake_vector(t, s.ai_embed_dim) for t in texts]
    out: list[list[float]] = []
    step = max(1, s.ai_embed_batch)  # 공급자마다 한 번에 받는 개수 제한이 있다
    for start in range(0, len(texts), step):
        batch = texts[start : start + step]
        body = {"model": s.ai_embed_model, "input": batch, "dimensions": s.ai_embed_dim}
        data = await _post(s.ai_embed_base_url or s.ai_base_url, s.ai_embed_api_key or s.ai_api_key,
                           "/embeddings", body, http)
        try:
            # 어떤 공급자(Gemini)는 index 0 을 빼고 보낸다 — 없으면 받은 순서로 본다
            rows = [r for _, r in sorted(enumerate(data["data"]), key=lambda t: t[1].get("index", t[0]))]
            out.extend(normalize(r["embedding"][: s.ai_embed_dim]) for r in rows)
        except (KeyError, TypeError) as e:
            raise AIUnavailable("임베딩 응답을 읽지 못했어요") from e
    return out


def normalize(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def cosine(a: list[float], b: list[float]) -> float:
    """둘 다 단위 벡터라 내적이 곧 코사인 유사도다."""
    return sum(x * y for x, y in zip(a, b, strict=True))


async def _post(base: str, key: str | None, path: str, body: dict, http: httpx.AsyncClient | None) -> dict:
    s = get_settings()
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    url = base.rstrip("/") + path
    for attempt in range(s.ai_retries + 1):
        try:
            if http is not None:
                r = await http.post(url, json=body, headers=headers, timeout=s.ai_timeout_seconds)
            else:
                async with httpx.AsyncClient(timeout=s.ai_timeout_seconds) as c:
                    r = await c.post(url, json=body, headers=headers)
        except httpx.HTTPError as e:
            raise AIUnavailable("AI 서버에 닿지 못했어요") from e
        if r.status_code not in (429, 503) or attempt == s.ai_retries:
            break
        # 무료 요금제의 분당 한도 — 서버가 알려 준 만큼(없으면 조금씩 늘려) 기다렸다 다시 보낸다
        await asyncio.sleep(_retry_after(r, attempt))
    if r.status_code >= 400:
        raise AIUnavailable(f"AI 서버가 {r.status_code} 를 돌려줬어요")
    return r.json()


def _retry_after(r: httpx.Response, attempt: int) -> float:
    try:
        wait = float(r.headers.get("retry-after", ""))
    except ValueError:
        wait = 2.0 * (2**attempt)
    return min(max(wait, 0.5), get_settings().ai_retry_max_seconds)


# --- 가짜 공급자: 같은 입력엔 늘 같은 출력 ---------------------------------------------


def _fake_vector(text: str, dim: int) -> list[float]:
    """글자 3-gram 해시를 쌓은 벡터 — 비슷한 글은 비슷한 방향을 가리켜 추천·검색 테스트가 의미 있다."""
    v = [0.0] * dim
    t = " " + " ".join(text.lower().split()) + " "
    for i in range(max(1, len(t) - 2)):
        h = int.from_bytes(hashlib.blake2b(t[i : i + 3].encode(), digest_size=8).digest(), "big")
        v[h % dim] += 1.0 if (h >> 32) & 1 else -1.0
    return normalize(v)


def _fake_chat(messages: list[dict], system: str | None) -> ChatResult:
    last = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
    text = f"(예시 답변) {str(last)[:120]}"
    return ChatResult(text, "fake", len(str(system or "")) + len(str(last)), len(text))
