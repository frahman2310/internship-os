"""Comments the user writes directly on a draft, and how they reach the drafting prompt.

A note is stronger evidence than an inferred diff, and is treated as such:

  * **No repetition threshold.** The learning loop waits for three edits before believing a
    pattern, because a single deletion might be a mood. A note is not a mood — it is him
    saying what he wants. It takes effect on the next draft.
  * **Quoted verbatim into the prompt.** His words, not a paraphrase of them. A summarised
    instruction is an instruction that has already drifted.
  * **Scoped.** Advice about one paragraph ("this opener is too soft") should not silently
    become a global rule, and a style rule ("never open with a question") should not be
    forgotten when he moves to the next firm. He chooses which.

Notes live in a JSON file rather than the database because they are free text attached to
a moment, not relational state, and because they must survive a schema change untouched.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..config import REPO_ROOT

NOTES_PATH = REPO_ROOT / "var" / "writing_notes.json"
MAX_QUOTE = 240


@dataclass
class Note:
    text: str                       # what the user wrote
    scope: str = "global"           # global | piece
    document_id: int | None = None
    quote: str = ""                 # the span he was commenting on, if any
    company: str = ""
    kind: str = ""
    created_at: str = ""
    resolved: bool = False

    def to_json(self) -> dict:
        return asdict(self)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_raw() -> list[dict]:
    if not NOTES_PATH.exists():
        return []
    try:
        data = json.loads(NOTES_PATH.read_text())
        return data if isinstance(data, list) else []
    except (ValueError, OSError):
        return []


def all_notes() -> list[Note]:
    return [Note(**{k: v for k, v in d.items() if k in Note.__annotations__})
            for d in _load_raw()]


def _save(notes: list[Note]) -> None:
    NOTES_PATH.parent.mkdir(parents=True, exist_ok=True)
    NOTES_PATH.write_text(json.dumps([n.to_json() for n in notes], indent=2, ensure_ascii=False))


def add(text: str, *, scope: str = "global", document_id: int | None = None,
        quote: str = "", company: str = "", kind: str = "") -> Note:
    text = (text or "").strip()
    if not text:
        raise ValueError("empty note")
    note = Note(
        text=text,
        scope="piece" if scope == "piece" else "global",
        document_id=document_id,
        quote=(quote or "").strip()[:MAX_QUOTE],
        company=company, kind=kind, created_at=_now(),
    )
    notes = all_notes()
    notes.append(note)
    _save(notes)
    return note


def for_piece(document_id: int | None) -> list[Note]:
    """Global advice plus anything written on this specific piece."""
    return [n for n in all_notes()
            if not n.resolved and (n.scope == "global" or n.document_id == document_id)]


def resolve(index: int) -> bool:
    notes = all_notes()
    if not 0 <= index < len(notes):
        return False
    notes[index].resolved = True
    _save(notes)
    return True


def guidance(document_id: int | None = None, limit: int = 15) -> str:
    """the user's own instructions, verbatim, as a prompt fragment.

    Newest first: when two notes conflict, the more recent one is what he thinks now.
    """
    relevant = [n for n in reversed(for_piece(document_id))][:limit]
    if not relevant:
        return ""
    lines = []
    for n in relevant:
        if n.quote:
            lines.append(f'on "{_trim(n.quote)}" — {n.text}')
        else:
            lines.append(n.text)
    return ("the user's own written instructions. These are explicit and take precedence over "
            "inferred style rules and over general guidance:\n  - " + "\n  - ".join(lines))


def _trim(quote: str, width: int = 70) -> str:
    q = re.sub(r"\s+", " ", quote).strip()
    return q if len(q) <= width else q[: width - 1] + "…"


def combined_guidance(document_id: int | None = None) -> str:
    """Everything the writing system knows about how the user wants to be written for.

    Order matters and encodes precedence: what he said, then what his edits show, then
    the general register. A later section never overrides an earlier one.
    """
    from .feedback import guidance as learned_guidance
    from .voice import REGISTER

    parts = [g for g in (guidance(document_id), learned_guidance(), REGISTER.strip()) if g]
    return "\n\n".join(parts)
