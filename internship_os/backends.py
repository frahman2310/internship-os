"""Where a given model id should actually be sent.

Both Gemini and Cerebras expose OpenAI-compatible endpoints, so a stored key lets us call
them directly and skip the gateway entirely — no provider plumbing, no shared free pool,
no combo routing to a model the upstream rejects.

Resolution is by prefix:
    gemini/<model>    -> Google AI Studio, using the `gemini` secret
    cerebras/<model>  -> Cerebras, using the `cerebras` secret
    groq/<model>      -> Groq, using the `groq` secret
    anything else     -> the local OmniRoute gateway
A prefixed model whose secret is missing is skipped, so a chain can list direct models
and still work before any key is added.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import settings
from .secrets import get_secret


@dataclass(frozen=True, slots=True)
class Backend:
    name: str
    base_url: str
    api_key: str
    model: str  # the id the upstream expects, prefix stripped


DIRECT = {
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "gemini"),
    "cerebras": ("https://api.cerebras.ai/v1", "cerebras"),
    "groq": ("https://api.groq.com/openai/v1", "groq"),
    "openrouter": ("https://openrouter.ai/api/v1", "openrouter"),
}


def resolve(model_id: str) -> Backend | None:
    """Return where to send this model, or None if it needs a key we do not have."""
    prefix, _, rest = model_id.partition("/")
    if prefix in DIRECT and rest:
        base_url, secret_name = DIRECT[prefix]
        key = get_secret(secret_name)
        if not key:
            return None  # no key yet: skip rather than fail the whole chain
        return Backend(name=prefix, base_url=base_url, api_key=key, model=rest)

    return Backend(
        name="omniroute",
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=model_id,
    )


def configured_direct() -> list[str]:
    """Which direct providers currently have a key. Used by `doctor`."""
    return [name for name, (_, secret) in DIRECT.items() if get_secret(secret)]
