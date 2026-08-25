"""the user's own writing, used to tell his habits apart from a model's defaults.

The blacklist's own caveat is that a match is a flag, not a delete — "an author's genuine
voice can override". To act on that you need evidence of what his voice actually is, and
that evidence has to be graded, because not every sample is equally his:

    creative_theosophy.txt   tier 1 — unassisted, written by him
    formal_finn1061.txt      tier 3 — declares "drafted with structural and editorial
                                      assistance from Claude (Anthropic, 2026)"

That distinction is not pedantry, it is the whole point. The formal essay runs 19 em dashes
in 1,503 words — one every 79 words. His unassisted writing has **zero** in 915. So the em
dash is Claude's fingerprint on that document, not his habit, and whitelisting it because
it appears in an AI-assisted sample would be circular: using AI text to license AI tells.

Only tier-1 evidence can override a rule. Even then, the loudest tells stay flagged: a
reader who sees "delve" concludes "AI" regardless of who actually typed it, and being right
about authorship does not help when the CV is already in the rejection pile.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ..config import REPO_ROOT

CORPUS_DIR = REPO_ROOT / "var" / "corpus"

# Tells so strongly associated with machine writing that a reader's conclusion does not
# depend on the truth. These are never overridden, however often he uses them.
NEVER_OVERRIDE = {
    "delve", "delve into", "tapestry", "testament", "a testament to", "landscape",
    "realm", "leverage", "utilize", "seamless", "seamlessly", "robust", "myriad",
    "plethora", "paradigm", "holistic", "navigate", "underscore", "underscores",
    "showcase", "showcasing", "pivotal", "foster", "fostering", "garner",
    "em-dash overuse", "resonate", "resonates", "cultivate", "harness", "beacon",
    "cornerstone", "intricate", "interplay", "vibrant", "profound",
}


@dataclass(frozen=True)
class Sample:
    path: Path
    register: str        # formal | creative
    tier: int            # 1 = unassisted; 3 = AI-assisted, weaker evidence of his voice
    note: str = ""

    def text(self) -> str:
        return self.path.read_text(encoding="utf-8") if self.path.exists() else ""


SAMPLES = [
    Sample(CORPUS_DIR / "creative_theosophy.txt", "creative", 1,
           "Unfiltered Theosophy essays — written unassisted"),
    Sample(CORPUS_DIR / "formal_finn1061.txt", "formal", 3,
           "FINN1061 essay — declares Claude editorial assistance, so surface habits "
           "(punctuation, connectives) are not reliable evidence of his voice"),
]


@dataclass
class RegisterProfile:
    register: str
    words: int
    sentences: int
    mean_sentence: float
    sentence_range: tuple[int, int]
    em_dash_per_1k: float
    semicolon_per_1k: float


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def profile(sample: Sample) -> RegisterProfile | None:
    text = sample.text()
    if not text:
        return None
    sents = _sentences(text)
    lens = [len(s.split()) for s in sents] or [0]
    words = max(1, len(text.split()))
    return RegisterProfile(
        register=sample.register,
        words=words,
        sentences=len(sents),
        mean_sentence=round(sum(lens) / len(lens), 1),
        sentence_range=(min(lens), max(lens)),
        em_dash_per_1k=round(text.count("—") * 1000 / words, 1),
        semicolon_per_1k=round(text.count(";") * 1000 / words, 1),
    )


@lru_cache(maxsize=1)
def author_habits() -> frozenset[str]:
    """Blacklist patterns evidenced in his UNASSISTED writing, minus the ones no reader
    will forgive. These are downgraded from tell to habit."""
    from .voice import rules

    literal, _ = rules()
    tier1 = "\n".join(s.text() for s in SAMPLES if s.tier == 1)
    if not tier1.strip():
        return frozenset()
    habits = set()
    for rule in literal:
        if rule.pattern.lower().strip() in NEVER_OVERRIDE:
            continue
        if any(p.strip().lower() in NEVER_OVERRIDE for p in rule.pattern.split("/")):
            continue
        if rule.regex.search(tier1):
            habits.add(rule.pattern)
    return frozenset(habits)


def summary() -> dict:
    profiles = {}
    for s in SAMPLES:
        p = profile(s)
        if p:
            profiles[s.register] = {
                "tier": s.tier, "words": p.words, "mean_sentence": p.mean_sentence,
                "sentence_range": list(p.sentence_range),
                "em_dash_per_1k": p.em_dash_per_1k, "note": s.note,
            }
    return {"profiles": profiles, "habits": sorted(author_habits())}
