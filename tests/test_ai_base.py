import httpx
import pytest

from app.core.config import get_settings
from app.services import ai


@pytest.fixture
def fake_ai(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "fake")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    get_settings.cache_clear()
    assert not ai.enabled()
    with pytest.raises(ai.AIUnavailable):
        await ai.chat([{"role": "user", "content": "안녕"}])
    get_settings.cache_clear()


async def test_fake_vectors_are_unit_and_similar_texts_are_closer(fake_ai):
    a, b, c = await ai.embed(["메밀꽃 필 무렵 이효석", "메밀꽃 필 무렵", "우주 로켓 엔진"])
    assert abs(sum(x * x for x in a) - 1) < 1e-9 and len(a) == get_settings().ai_embed_dim
    assert ai.cosine(a, b) > ai.cosine(a, c)


async def test_openai_compatible_call_shape(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai_compat")
    monkeypatch.setenv("AI_API_KEY", "k")
    monkeypatch.setenv("AI_BASE_URL", "https://llm.example/v1")
    get_settings.cache_clear()
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen[req.url.path] = (req.headers.get("authorization"), req.read())
        if req.url.path.endswith("/chat/completions"):
            return httpx.Response(200, json={"model": "m", "choices": [{"message": {"content": " 답 "}}],
                                             "usage": {"prompt_tokens": 3, "completion_tokens": 1}})
        return httpx.Response(200, json={"data": [{"index": 1, "embedding": [0, 2]}, {"index": 0, "embedding": [3, 4]}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        r = await ai.chat([{"role": "user", "content": "q"}], system="s", http=http)
        v = await ai.embed(["x", "y"], http=http)
    assert r.text == "답" and r.input_tokens == 3
    assert seen["/v1/chat/completions"][0] == "Bearer k"
    assert v[0] == [0.6, 0.8] and v[1] == [0.0, 1.0]
    get_settings.cache_clear()


async def test_provider_error_becomes_unavailable(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai_compat")
    monkeypatch.setenv("AI_API_KEY", "k")
    get_settings.cache_clear()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(429))) as http:
        with pytest.raises(ai.AIUnavailable):
            await ai.chat([{"role": "user", "content": "q"}], http=http)
    get_settings.cache_clear()
