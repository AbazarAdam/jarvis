import pytest

from core.model_router import ModelRouter


@pytest.fixture()
def router():
    r = ModelRouter()
    r._cooldowns.clear()
    return r


def test_chat_uses_first_provider(monkeypatch, router):
    monkeypatch.setattr(
        router,
        "_try_openrouter",
        lambda messages, temperature, max_tokens: "openrouter result",
    )
    monkeypatch.setattr(
        router,
        "_try_groq",
        lambda messages, temperature, max_tokens: "groq result",
    )

    result = router.chat(
        [{"role": "user", "content": "hello"}],
        providers=["openrouter", "groq"],
    )

    assert result["success"] is True
    assert result["provider"] == "openrouter"
    assert result["text"] == "openrouter result"


def test_chat_falls_back_to_next_provider(monkeypatch, router):
    def fail_openrouter(messages, temperature, max_tokens):
        raise RuntimeError("openrouter failed")

    monkeypatch.setattr(router, "_try_openrouter", fail_openrouter)
    monkeypatch.setattr(
        router,
        "_try_groq",
        lambda messages, temperature, max_tokens: "groq result",
    )

    result = router.chat(
        [{"role": "user", "content": "hello"}],
        providers=["openrouter", "groq"],
    )

    assert result["success"] is True
    assert result["provider"] == "groq"


def test_chat_sets_cooldown_on_rate_limit(monkeypatch, router):
    def rate_limit(messages, temperature, max_tokens):
        raise RuntimeError("rate limit exceeded")

    monkeypatch.setattr(router, "_try_openrouter", rate_limit)
    monkeypatch.setattr(router, "_try_groq", rate_limit)

    result = router.chat(
        [{"role": "user", "content": "hello"}],
        providers=["openrouter", "groq"],
    )

    assert result["success"] is False
    assert router._in_cooldown("openrouter") is True
    assert router._in_cooldown("groq") is True