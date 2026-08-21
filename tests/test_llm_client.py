"""LLM client behaviour, proven without a single real network call.

The offline/cache tests matter beyond correctness: they are why the rest of the suite can
run without burning tokens, and why a demo works on a plane.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from internship_os.config import settings
from internship_os.llm import client as llm
from internship_os.models.infra import LlmCall


@pytest.fixture
def patched(engine, monkeypatch):
    """Point the client's persistence at the throwaway test DB."""
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    @asynccontextmanager
    async def _scope():
        async with factory() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    monkeypatch.setattr(llm, "session_scope", _scope)
    monkeypatch.setattr(settings, "llm_offline", False)
    return factory


# Captured before any monkeypatching: patching `llm.httpx` patches the httpx module
# itself, so a factory that called httpx.AsyncClient() would recurse into itself.
_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _mock_client(handler):
    """Return a factory that builds a real AsyncClient wired to a MockTransport."""

    def factory(*_args, **kwargs):
        kwargs.pop("timeout", None)
        kwargs.pop("transport", None)
        return _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler), **kwargs)

    return factory


def _reply(text: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": text}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        },
    )


MESSAGES = [{"role": "user", "content": "Does Jane Street sponsor visas?"}]


class TestFailover:
    async def test_falls_through_to_the_next_model(self, patched, monkeypatch):
        """Free-tier endpoints flake; the chain exists precisely for that."""
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            import json

            model = json.loads(request.content)["model"]
            seen.append(model)
            if model == "flaky/one":
                return httpx.Response(500, json={"error": "upstream exploded"})
            return _reply("yes, they sponsor")

        monkeypatch.setattr(llm.httpx, "AsyncClient", _mock_client(handler))
        result = await llm.chat(MESSAGES, models=["flaky/one", "good/two"])

        assert result.text == "yes, they sponsor"
        assert result.model == "good/two"
        assert seen == ["flaky/one", "good/two"], "should try models in order"

    async def test_all_models_failing_raises(self, patched, monkeypatch):
        def handler(_request):
            return httpx.Response(503, json={"error": "down"})

        monkeypatch.setattr(llm.httpx, "AsyncClient", _mock_client(handler))
        with pytest.raises(llm.LlmUnavailable, match="all 2 models failed"):
            await llm.chat(MESSAGES, models=["a/one", "b/two"])


class TestCache:
    async def test_identical_prompt_hits_cache_and_makes_one_call(self, patched, monkeypatch):
        calls = {"n": 0}

        def handler(_request):
            calls["n"] += 1
            return _reply("cached answer")

        monkeypatch.setattr(llm.httpx, "AsyncClient", _mock_client(handler))

        first = await llm.chat(MESSAGES, models=["m/one"])
        second = await llm.chat(MESSAGES, models=["m/one"])

        assert calls["n"] == 1, "second call should be served from cache"
        assert first.cache_hit is False
        assert second.cache_hit is True
        assert second.text == first.text

    async def test_call_is_logged(self, patched, monkeypatch):
        monkeypatch.setattr(
            llm.httpx, "AsyncClient", _mock_client(lambda _r: _reply("logged"))
        )
        await llm.chat(MESSAGES, models=["m/one"], purpose="unit-test")

        async with patched() as s:
            n = await s.scalar(select(func.count()).select_from(LlmCall))
            row = (await s.execute(select(LlmCall))).scalars().first()
        assert n == 1
        assert row.purpose == "unit-test"
        assert row.prompt_tokens == 10


class TestOffline:
    async def test_offline_uses_cache_when_warm(self, patched, monkeypatch):
        monkeypatch.setattr(
            llm.httpx, "AsyncClient", _mock_client(lambda _r: _reply("warm"))
        )
        await llm.chat(MESSAGES, models=["m/one"])

        monkeypatch.setattr(settings, "llm_offline", True)
        result = await llm.chat(MESSAGES, models=["m/one"])
        assert result.cache_hit is True and result.text == "warm"

    async def test_offline_raises_on_cold_cache(self, patched, monkeypatch):
        monkeypatch.setattr(settings, "llm_offline", True)

        def explode(*_a, **_k):
            raise AssertionError("offline mode must never open a connection")

        monkeypatch.setattr(llm.httpx, "AsyncClient", explode)
        with pytest.raises(llm.LlmUnavailable, match="offline mode"):
            await llm.chat([{"role": "user", "content": "never asked before"}])


class TestPromptHash:
    async def test_hash_is_stable_and_sensitive(self):
        a = llm.prompt_hash(MESSAGES, 0.4, 100)
        assert a == llm.prompt_hash(MESSAGES, 0.4, 100)
        assert a != llm.prompt_hash(MESSAGES, 0.5, 100)
        assert a != llm.prompt_hash(
            [{"role": "user", "content": "different"}], 0.4, 100
        )
