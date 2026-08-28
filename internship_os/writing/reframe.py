"""Regenerate an existing piece against the accumulated rules.

The point of the feedback loop is that rules keep arriving after a draft was written. This
rewrites a finished piece so it obeys everything learned since, without going back to the
evidence store.

That distinction matters. Redrafting from evidence would let the model choose different
material, so a reframe could quietly change what the piece claims. Reframing works from the
existing text: same facts, same argument, different prose. Two guards enforce it:

  * **No new numbers.** Any figure absent from the original is a fabrication, checked with
    the same function that guards the per-bullet sharpen feature.
  * **No new named entities.** A capitalised name that was not in the original cannot have
    come from anywhere legitimate.

Nothing is saved automatically. The result is returned as a proposal for the user to accept or
discard, because a rewrite he has not read is exactly the thing this system exists to
prevent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from ..llm.schema_call import call_schema
from ..editor.assist import invented_numbers
from .notes import combined_guidance
from .standards import check
from .voice import report as voice_report

# Words that are capitalised for position or convention rather than because they name
# something, so their appearance in a rewrite is not evidence of invention.
_COMMON_CAPS = {
    "I", "The", "A", "An", "In", "On", "At", "By", "For", "With", "That", "This", "It",
    "My", "We", "He", "She", "They", "But", "And", "Or", "So", "What", "When", "Where",
    "Which", "While", "After", "Before", "Having", "Building", "Working", "One", "Two",
    "Three", "Their", "There", "Then", "Thank", "Yours", "Dear", "Sir", "Madam",
}


class Reframed(BaseModel):
    text: str = Field(description="The rewritten piece, obeying every rule given")
    changes: list[str] = Field(
        default_factory=list, description="What was changed and why, one clause each")


@dataclass
class ReframeResult:
    original: str
    text: str
    changes: list[str] = field(default_factory=list)
    invented_numbers: list[str] = field(default_factory=list)
    invented_entities: list[str] = field(default_factory=list)
    findings_before: list[str] = field(default_factory=list)
    findings_after: list[str] = field(default_factory=list)
    tells_after: int = 0

    @property
    def safe(self) -> bool:
        return not self.invented_numbers and not self.invented_entities

    @property
    def improved(self) -> bool:
        return len(self.findings_after) <= len(self.findings_before)

    def to_json(self) -> dict:
        return {
            "text": self.text, "changes": self.changes,
            "invented_numbers": self.invented_numbers,
            "invented_entities": self.invented_entities,
            "findings_before": self.findings_before,
            "findings_after": self.findings_after,
            "tells_after": self.tells_after,
            "safe": self.safe, "improved": self.improved,
        }


def named_entities(text: str) -> set[str]:
    """Capitalised words that actually name something.

    A word is skipped when it opens a sentence, because that capital is grammar rather
    than a name: an early version flagged "Executing" at the start of a rewritten sentence
    as an invented entity and blocked an otherwise clean reframe.
    """
    text = text or ""
    # Offsets where a sentence begins, so capitals there can be ignored.
    starts = {0}
    for m in re.finditer(r"(?:[.!?]\s+|\n\s*)", text):
        starts.add(m.end())
    out = set()
    for m in re.finditer(r"\b([A-Z][A-Za-z&'.\-]{2,})", text):
        if m.start() in starts:
            continue
        word = m.group(1)
        if word in _COMMON_CAPS:
            continue
        out.add(word.rstrip(".,;:"))
    return out


async def reframe(
    text: str,
    *,
    document_id: int | None = None,
    kind: str = "cover_letter",
    company: str = "",
    title: str = "",
    competency: str = "cover_letter",
) -> ReframeResult:
    before = [f"{f.rule}: {f.detail}" for f in check(
        text, company=company, competency=competency, letter=kind == "cover_letter")]
    guidance = combined_guidance(document_id, kind=kind)

    problems = ("\n".join(f"  - {b}" for b in before)
                if before else "  - (nothing mechanical; apply the rules for tone and flow)")
    prompt = (
        f"{guidance}\n\n"
        f"Rewrite the piece below so that it obeys every rule above.\n"
        f"Piece: {title or kind} for {company or 'this employer'}.\n\n"
        f"Mechanical checks currently failing:\n{problems}\n\n"
        "--- CURRENT TEXT ---\n"
        f"{text}\n"
        "--- END ---\n\n"
        "Keep every fact, figure, name and claim exactly as it stands. You may reorder, "
        "rejoin and rephrase freely, and you should, but you may not introduce any number, "
        "company, person or achievement that is not already in the text above, and you may "
        "not remove a fact to make the prose easier. Return the full rewritten piece and a "
        "short list of what you changed."
    )
    result = await call_schema(
        prompt, Reframed, system="You rewrite application prose to a strict rule set.",
        purpose="writing:reframe", temperature=0.3, max_tokens=3000,
    )
    new = result.text.strip()

    after = [f"{f.rule}: {f.detail}" for f in check(
        new, company=company, competency=competency, letter=kind == "cover_letter")]
    tells = voice_report(new)
    # The employer's own name cannot be an invention in a piece written to that employer.
    # A reframe that names Bank of America in a Bank of America answer is adding context,
    # which is exactly what the swap test asks for elsewhere in this system.
    allowed = {w.strip(".,;:") for w in re.findall(r"[A-Za-z&'.\-]{2,}", company or "")}
    return ReframeResult(
        original=text, text=new, changes=[c.strip() for c in result.changes][:8],
        invented_numbers=invented_numbers(text, new),
        invented_entities=sorted(named_entities(new) - named_entities(text) - allowed),
        findings_before=before, findings_after=after,
        tells_after=len(tells["strong"]) + len(tells["medium"]),
    )
