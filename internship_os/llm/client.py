"""OpenAI-compatible LLM client, pointed at the local OmniRoute gateway by default.

Ported from ~/Automation for Research & Outreach Pipeline/llm.py, which already had the
right shape: no SDK, a comma-separated failover chain because free-tier providers flake.
Added here: response caching, call logging, and an offline mode.

Caching is keyed on (prompt_sha256, model) in `llm_calls` — the log IS the cache, so
there's no second table to keep consistent. A deterministic re-run costs nothing, which is
what makes the test suite runnable without burning tokens.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass

import httpx
from sqlalchemy import select

from ..config import settings
from ..db import session_scope
from ..models.infra import LlmCall


class LlmUnavailable(RuntimeError):
    """Every model in the chain failed, or the client is offline with no cached answer."""


@dataclass(slots=True)
class LlmResult:
    text: str
    model: str
    cache_hit: bool
    latency_ms: int


def _chat_url() -> str:
    base = settings.llm_base_url.rstrip("/")
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


async def chat(
    messages: list[dict],
    *,
    models: list[str] | None = None,
    temperature: float = 0.4,
    max_tokens: int = 1200,
    purpose: str | None = None,
    use_cache: bool = True,
) -> LlmResult:
    """Return assistant text, trying each model in the chain until one answers."""
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
            "Run once online to populate the cache, or pass use_cache=True with a warm cache."
        )

    failures: list[str] = []
    async with httpx.AsyncClient(timeout=settings.llm_timeout_s) as client:
        for model in chain:
            started = time.perf_counter()
            try:
                r = await client.post(
                    _chat_url(),
                    headers={
                        "Authorization": f"Bearer {settings.llm_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": messages,
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                        # The gateway streams SSE unless told otherwise.
                        "stream": False,
                    },
                )
                r.raise_for_status()
                body = r.json()
                text = (body["choices"][0]["message"].get("content") or "").strip()
                if not text:
                    # Reasoning models can spend the whole budget on `reasoning_content`
                    # and return empty content. That is a failure, so fail over rather
                    # than hand an empty string to a schema parser.
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
                    cost_usd=0.0 if _is_free(model) else 0.0,
                )
                return LlmResult(text=text, model=model, cache_hit=False, latency_ms=latency)
            except httpx.HTTPStatusError as exc:
                failures.append(f"{model}: HTTP {exc.response.status_code}")
                continue
            except (httpx.HTTPError, KeyError, ValueError, IndexError) as exc:
                failures.append(f"{model}: {type(exc).__name__} {str(exc)[:60]}")
                continue

    # Report every model's own failure: "all models failed" plus one status code hides
    # whether the gateway is down, out of quota, or rejecting a specific provider.
    raise LlmUnavailable(
        f"all {len(chain)} model(s) failed at {settings.llm_base_url}\n  "
        + "\n  ".join(failures)
    )
