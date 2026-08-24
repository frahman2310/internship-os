"""OpenAI-compatible LLM client, pointed at the local OmniRoute gateway by default.

Ported from ~/Automation for Research & Outreach Pipeline/llm.py, which already had the
right shape: no SDK, a comma-separated failover chain because free-tier providers flake.
Added here: response caching, call logging, and an offline mode.

Caching is keyed on (prompt_sha256, model) in `llm_calls` — the log IS the cache, so
there's no second table to keep consistent. A deterministic re-run costs nothing, which is
what makes the test suite runnable without burning tokens.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass

import httpx
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from ..backends import resolve
from ..config import settings
from ..db import session_scope
from ..models.infra import LlmCall
from ..secrets import redact


class LlmUnavailable(RuntimeError):
    """Every model in the chain failed, or the client is offline with no cached answer."""


@dataclass(slots=True)
class LlmResult:
    text: str
    model: str
    cache_hit: bool
    latency_ms: int


def _chat_url(base_url: str | None = None) -> str:
    base = (base_url or settings.llm_base_url).rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def prompt_hash(messages: list[dict], temperature: float, max_tokens: int) -> str:
    blob = json.dumps(
        {"m": messages, "t": temperature, "mt": max_tokens}, sort_keys=True, ensure_ascii=False
    )
    return hashlib.sha256(blob.encode()).hexdigest()


async def list_models() -> list[str]:
    """Ask the gateway what it can route to. Used by `doctor` to validate config."""
    base = settings.llm_base_url.rstrip("/")
    url = base if base.endswith("/models") else f"{base}/models"
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(url, headers={"Authorization": f"Bearer {settings.llm_api_key}"})
        r.raise_for_status()
        return [m["id"] for m in r.json().get("data", [])]


async def _cached(phash: str, models: list[str]) -> tuple[str, str] | None:
    try:
        return await _cached_inner(phash, models)
    except OperationalError:
        return None


async def _cached_inner(phash: str, models: list[str]) -> tuple[str, str] | None:
    async with session_scope() as s:
        row = (
            await s.execute(
                select(LlmCall.response, LlmCall.model).where(
                    LlmCall.prompt_sha256 == phash,
                    LlmCall.model.in_(models),
                    LlmCall.response.is_not(None),
                )
            )
        ).first()
    return (row[0], row[1]) if row else None


async def _log(
    *,
    phash: str,
    model: str,
    response: str | None,
    purpose: str | None,
    latency_ms: int,
    cache_hit: bool,
    usage: dict | None,
    cost_usd: float,
) -> None:
    """Best-effort. SQLite allows a single writer, and a long-running research run holds
    the write lock on the caller's session; losing a cache entry is an acceptable cost,
    losing the run is not."""
    try:
        await _log_inner(
            phash=phash, model=model, response=response, purpose=purpose,
            latency_ms=latency_ms, cache_hit=cache_hit, usage=usage, cost_usd=cost_usd,
        )
    except OperationalError:
        return


async def _log_inner(
    *,
    phash: str,
    model: str,
    response: str | None,
    purpose: str | None,
    latency_ms: int,
    cache_hit: bool,
    usage: dict | None,
    cost_usd: float,
) -> None:
    async with session_scope() as s:
        exists = await s.scalar(
            select(LlmCall.id).where(
                LlmCall.prompt_sha256 == phash, LlmCall.model == model
            )
        )
        if exists:
            return
        s.add(
            LlmCall(
                purpose=purpose,
                model=model,
                prompt_sha256=phash,
                response=response,
                prompt_tokens=(usage or {}).get("prompt_tokens"),
                completion_tokens=(usage or {}).get("completion_tokens"),
                cost_usd=cost_usd,
                latency_ms=latency_ms,
                cache_hit=cache_hit,
            )
        )


def _is_free(model: str) -> bool:
    return "free" in model.lower()



# Statuses meaning "this provider might serve it later": rate limits and pool exhaustion.
RETRYABLE_STATUS = {429, 502, 503, 504}


async def _one_pass(
    client: httpx.AsyncClient,
    chain: list[str],
    messages: list[dict],
    temperature: float,
    max_tokens: int,
    phash: str,
    purpose: str | None,
    failures: list[str],
) -> tuple[LlmResult | None, bool]:
    """Try every model once. Returns (result, whether any failure looked temporary)."""
    saw_retryable = False
    for model in chain:
        backend = resolve(model)
        if backend is None:
            # A direct-provider model whose key is not set yet. Skip quietly so a chain
            # can name future providers without breaking today.
            continue
        started = time.perf_counter()
        try:
            r = await client.post(
                _chat_url(backend.base_url),
                headers=backend.headers(),
                json={
                    "model": backend.model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "stream": False,  # the gateway streams SSE unless told otherwise
                },
            )
            r.raise_for_status()
            body = r.json()
            text = (body["choices"][0]["message"].get("content") or "").strip()
            if not text:
                # Reasoning models can spend the entire budget on `reasoning_content`
                # and return empty content. Treat as failure rather than hand an empty
                # string to a schema parser.
                raise ValueError(f"{model} returned empty content")
            latency = int((time.perf_counter() - started) * 1000)
            await _log(
                phash=phash,
                model=model,
                response=text,
                purpose=purpose,
                latency_ms=latency,
                cache_hit=False,
                usage=body.get("usage"),
                cost_usd=0.0,
            )
            return LlmResult(text=text, model=model, cache_hit=False, latency_ms=latency), False
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            saw_retryable = saw_retryable or status in RETRYABLE_STATUS
            failures.append(f"{model}: HTTP {status}")
        except (httpx.HTTPError, KeyError, ValueError, IndexError) as exc:
            saw_retryable = saw_retryable or isinstance(exc, httpx.TimeoutException)
            failures.append(redact(f"{model}: {type(exc).__name__} {str(exc)[:60]}"))
    return None, saw_retryable


async def chat(
    messages: list[dict],
    *,
    models: list[str] | None = None,
    temperature: float = 0.4,
    max_tokens: int = 1200,
    purpose: str | None = None,
    use_cache: bool = True,
) -> LlmResult:
    """Return assistant text, trying each model in the chain until one answers.

    A whole chain can fail with 429/503 when free provider pools drain — which is a
    "later", not a "no". In that case the chain is retried after a short backoff rather
    than failing a research run outright.
    """
    chain = models or settings.research_model_chain()
    if not chain:
        raise LlmUnavailable("no model chain configured")

    phash = prompt_hash(messages, temperature, max_tokens)

    if use_cache:
        hit = await _cached(phash, chain)
        if hit:
            return LlmResult(text=hit[0], model=hit[1], cache_hit=True, latency_ms=0)

    if settings.llm_offline:
        raise LlmUnavailable(
            "offline mode: no cached response for this prompt. "
            "Run once online to populate the cache."
        )

    failures: list[str] = []
    async with httpx.AsyncClient(timeout=settings.llm_timeout_s) as client:
        for attempt in range(max(1, settings.llm_max_passes)):
            if attempt:
                await asyncio.sleep(settings.llm_retry_delay_s * (2 ** (attempt - 1)))
                failures.append(f"-- retry pass {attempt + 1} --")
            result, retryable = await _one_pass(
                client, chain, messages, temperature, max_tokens, phash, purpose, failures
            )
            if result is not None:
                return result
            if not retryable:
                break

    # Each model's own failure is reported: one aggregate status code hides whether the
    # gateway is down, out of quota, or rejecting one provider.
    raise LlmUnavailable(
        redact(
            f"all {len(chain)} model(s) failed\n  " + "\n  ".join(failures)
        )
    )
