"""Ask a model for JSON matching a pydantic model, and retry once with the error attached.

One retry, not a loop: if a model cannot produce the shape twice, the prompt is wrong and
looping just spends tokens confirming it.
"""

from __future__ import annotations

import json
import re
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from ..config import settings
from .client import LlmUnavailable, chat

T = TypeVar("T", bound=BaseModel)

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> str:
    """Models wrap JSON in prose and fences however they like. Take the first real object."""
    fenced = _FENCE.search(text)
    if fenced:
        return fenced.group(1).strip()
    start = min(
        (i for i in (text.find("{"), text.find("[")) if i != -1), default=-1
    )
    if start == -1:
        return text.strip()
    closer = "}" if text[start] == "{" else "]"
    end = text.rfind(closer)
    return text[start : end + 1] if end > start else text[start:].strip()


async def call_schema(
    prompt: str,
    schema: type[T],
    *,
    system: str | None = None,
    models: list[str] | None = None,
    purpose: str | None = None,
    temperature: float = 0.2,
    max_tokens: int = 2000,
) -> T:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    instruction = (
        f"{prompt}\n\nReply with JSON only, matching this schema:\n"
        f"{json.dumps(schema.model_json_schema(), indent=2)}"
    )
    messages.append({"role": "user", "content": instruction})

    chain = models or settings.research_model_chain()
    result = await chat(
        messages, models=chain, temperature=temperature, max_tokens=max_tokens, purpose=purpose
    )
    try:
        return schema.model_validate_json(extract_json(result.text))
    except (ValidationError, ValueError) as first_error:
        retry = messages + [
            {"role": "assistant", "content": result.text},
            {
                "role": "user",
                "content": (
                    f"That did not validate: {first_error}\n"
                    "Reply again with JSON only, no prose, matching the schema exactly."
                ),
            },
        ]
        retried = await chat(
            retry,
            models=chain,
            temperature=0.0,
            max_tokens=max_tokens,
            purpose=f"{purpose}:retry",
            use_cache=False,
        )
        try:
            return schema.model_validate_json(extract_json(retried.text))
        except (ValidationError, ValueError) as second_error:
            raise LlmUnavailable(
                f"model could not produce {schema.__name__} twice: {second_error}"
            ) from second_error
