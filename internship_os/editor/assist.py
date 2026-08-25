"""AI assistance for CV bullets, held to the house style of the reference CVs.

The style is not invented here — it is read off the two examples supplied (a Columbia
finance CV and an Oxford PPE CV), which agree on the shape of a good bullet:

    [strong verb] [quantified object] [mechanism / tool] [purpose or outcome]

    "Creating financial projection models on 3 M&A Deals with 10M+ valuations for
     potential acquisitions in Fintech, HR-Tech and Payments industries."
    "Ran a simulated trading competition, finishing 2nd out of 31 participants,
     primarily using stochastic oscillators and the RSI to identify oversold companies."

The one hard rule: **a suggestion may not introduce a number the original did not have.**
Rewriting is a language operation; inventing a metric is fabrication, and a model asked to
"make this more impressive" will reach for a number every time. Suggestions that do are
dropped before you ever see them.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field

from ..documents.cover import numbers_in
from ..llm.schema_call import call_schema

HOUSE_STYLE = """
Rules, taken from strong UK/US finance and tech internship CVs:

1. Open with a verb. Present participle for a role still held ("Leading", "Building"),
   simple past for a finished one ("Led", "Built"). Never "Responsible for", never "I".
2. Quantify wherever the source supports it: how many, how much, how often, what share.
   A bullet with a real number beats an adjective every time.
3. Name the mechanism — the tool, method, or source ("by studying public 10Ks",
   "using Databricks and Docker", "with a competitor matrix").
4. End with the purpose or the outcome, not the activity ("to inform the financing
   recommendation", "increasing it by 46%").
5. One to two lines. Cut hedges, cut "various", cut "helped with", cut "successfully".
6. No self-description: no "passionate", "motivated", "detail-oriented", "hard-working".
7. British spelling (analyse, modelling, organised) unless the employer is American.
"""


class Variant(BaseModel):
    text: str = Field(description="The rewritten bullet")
    rationale: str = Field(description="What changed, in one short clause")


class Suggestions(BaseModel):
    variants: list[Variant] = Field(default_factory=list)


class Critique(BaseModel):
    verdict: str = Field(description="strong | workable | weak")
    problems: list[str] = Field(default_factory=list, description="Concrete, max 3")


@dataclass
class Suggestion:
    text: str
    rationale: str
    invented_numbers: list[str]

    @property
    def safe(self) -> bool:
        return not self.invented_numbers


def invented_numbers(original: str, variant: str) -> list[str]:
    """Numbers the rewrite added that the source never had. This is the whole safety
    property of the editor: sharpening language is fine, inventing a metric is not."""
    return sorted(numbers_in(variant) - numbers_in(original))


async def rewrite(bullet: str, *, role: str = "", org: str = "", n: int = 3) -> list[Suggestion]:
    """Return rewrites of one bullet. Any variant that invents a number is discarded."""
    prompt = (
        f"{HOUSE_STYLE}\n\n"
        f"Role: {role or '(unspecified)'} at {org or '(unspecified)'}\n"
        f"Current bullet:\n{bullet}\n\n"
        f"Rewrite it {n} ways, strongest first. Use ONLY facts present in the bullet "
        "above. You may reorder, sharpen verbs, cut padding and surface a number that is "
        "already there. You may NOT add a number, a tool, a client name, or an outcome "
        "that is not already stated — if the bullet lacks a metric, leave it without one."
    )
    result = await call_schema(
        prompt, Suggestions, system="You edit CV bullet points.", purpose="editor:rewrite",
        temperature=0.5, max_tokens=2000,
    )
    out = []
    for variant in result.variants[:n]:
        out.append(
            Suggestion(
                variant.text.strip(),
                variant.rationale.strip(),
                invented_numbers(bullet, variant.text),
            )
        )
    return out


async def critique(bullet: str) -> Critique:
    """Say what is weak about a bullet without rewriting it — useful when you want to fix
    it yourself and just need to know what a reader would object to."""
    prompt = (
        f"{HOUSE_STYLE}\n\nBullet:\n{bullet}\n\n"
        "Judge it against the rules. Be specific and blunt: name the weak verb, the "
        "missing quantity, the padding. If it is already strong, say so and list nothing."
    )
    return await call_schema(
        prompt, Critique, system="You review CV bullet points.", purpose="editor:critique",
        temperature=0.2, max_tokens=1200,
    )
