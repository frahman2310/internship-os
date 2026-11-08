"""The house voice: a deterministic AI-tell scanner compiled from the user's Writing OS.

Two decisions worth stating.

**The blacklist runs as regex, not as a prompt.** Asking a model to avoid its own tells is
unreliable — the tells are what it reverse-defaults to under pressure. A compiled pass over
the finished text catches them every time, costs nothing, and is auditable.

**VOICE_DNA.md is deliberately NOT used for register.** It was reverse-engineered from two
confessional-philosophical essays; its habits (comma splices, anaphora, lowercase openings,
"let uncertainty stand") are exactly wrong for a Goldman Sachs application. Only its rhythm
guidance survives here — vary sentence length, never manufacture confidence.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

BLACKLIST_CSV = Path.home() / "Add On's" / "AI_WRITING_BLACKLIST.csv"

# Types whose "Item or Pattern" is a literal to match. The rest describe a *shape*
# (rule of three, false agency) that a regex cannot see; those are passed to the reviewer
# as advisory notes instead of being silently dropped.
LITERAL_TYPES = {
    "Word", "Phrase", "Connector", "Rhetorical Setup", "Participial Tail",
    "Passive Voice", "Copula Avoidance", "Dramatic Fragment", "Narrator Distance",
    "Formulaic Section",
}

# Register rules for application writing. Not from VOICE_DNA — that file's voice is wrong
# for this context — but from what the reference CVs and cover letters actually do.
REGISTER = """
Write like a competent person stating what they did. Specifically:
  * First person, past tense for finished work, present for current.
  * Vary sentence length. Two long sentences in a row, then a short one.
  * No manufactured enthusiasm: never "thrilled", "passionate", "excited to".
  * Never claim a feeling about the employer you cannot evidence.
  * Concrete nouns and real numbers over adjectives.
  * British spelling.
"""


@dataclass(frozen=True)
class Rule:
    category: str
    type: str
    pattern: str
    alternative: str
    severity: str
    regex: re.Pattern


# Domain vocabulary that collides with the blacklist. "Leverage" is business jargon as a
# verb and a technical noun in finance — flagging "leverage multiples" would push the user to
# weaken correct terminology in front of people who use it daily. Same for the rest.
DOMAIN_EXCEPTIONS = re.compile(
    r"""(?ix)
    leverag\w*\s+(?:multiple|ratio|level|sizing|finance|loan|buyout|point)s?
  | (?:senior|net|gross|total)\s+leverage
  | leveraged\s+(?:finance|buyout|loan|credit)
  | robust(?:ness)?\s+(?:test|check|standard\s+error)s?
  | scal\w+\s+law
  | comprehensive\s+income
  # "bandwidth" as a literal hardware quantity, not the diary-capacity metaphor. The user's
  # answer on FlashAttention is about memory bandwidth being the real bottleneck, which is
  # the correct technical term and the whole point of the paragraph.
  | (?:memory|network|gpu|on[- ]chip|available)\s+bandwidth
  | bandwidth\s+(?:bottleneck|bound|limit|constraint)
  | bandwidth\s+that\s+is\s+the
  # "leverage" as bargaining power, distinct from both the finance noun and the AI verb.
  | (?:lose|loses|losing|lost|gain|gains|gaining|retain|retains)\s+leverage
  | leverage\s+over
  """
)


@dataclass
class Hit:
    matched: str
    rule: Rule

    @property
    def severity(self) -> str:
        return self.rule.severity

    def __str__(self) -> str:
        alt = f" → {self.rule.alternative}" if self.rule.alternative else ""
        return f"{self.matched!r} ({self.rule.category}, {self.rule.severity}){alt}"


def _compile(item: str) -> re.Pattern | None:
    """Turn a blacklist entry into a matcher.

    Entries carry two conventions: '/' separates alternatives ('delve / delve into'), and
    parentheses hold a usage hint rather than literal text ('navigate (challenges)').
    """
    alternatives = []
    for part in item.split("/"):
        part = re.sub(r"\([^)]*\)", "", part).strip()
        if not part or part.lower().startswith(("inanimate", "forced", "three-beat")):
            continue
        if len(part.split()) > 8:
            continue
        alternatives.append(re.escape(part).replace(r"\ ", r"\s+"))
    if not alternatives:
        return None
    body = "|".join(sorted(alternatives, key=len, reverse=True))
    return re.compile(rf"(?<!\w)(?:{body})(?!\w)", re.IGNORECASE)


@lru_cache(maxsize=1)
def rules() -> tuple[list[Rule], list[str]]:
    """(literal rules, advisory shape-notes). Cached: the CSV is read once per process."""
    literal: list[Rule] = []
    advisory: list[str] = []
    if not BLACKLIST_CSV.exists():
        return literal, advisory
    with BLACKLIST_CSV.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            item = (row.get("Item or Pattern") or "").strip()
            rtype = (row.get("Type") or "").strip()
            if not item:
                continue
            if rtype in LITERAL_TYPES:
                rx = _compile(item)
                if rx is not None:
                    literal.append(Rule(
                        category=(row.get("Category") or "").strip(),
                        type=rtype,
                        pattern=item,
                        alternative=(row.get("Human Alternative") or "").strip(),
                        severity=(row.get("Severity") or "Medium").strip(),
                        regex=rx,
                    ))
                    continue
            advisory.append(f"{rtype}: {item}" + (f" (use: {row['Human Alternative']})"
                                                  if row.get("Human Alternative") else ""))
    return literal, advisory


def scan(text: str, *, respect_habits: bool = True) -> list[Hit]:
    """Every AI tell in the text, strongest first.

    `respect_habits` drops rules that the user's own unassisted writing demonstrably uses —
    the blacklist's stated caveat that a genuine authorial voice overrides a match. The
    loudest tells are never overridden; see corpus.NEVER_OVERRIDE for why.
    """
    literal, _ = rules()
    if respect_habits:
        from .corpus import author_habits

        habits = author_habits()
        literal = [r for r in literal if r.pattern not in habits]
    hits: list[Hit] = []
    exempt = {(m.start(), m.end()) for m in DOMAIN_EXCEPTIONS.finditer(text)}
    for rule in literal:
        for m in rule.regex.finditer(text):
            # Skip a hit that sits inside a legitimate domain phrase.
            if any(a <= m.start() and m.end() <= b for a, b in exempt):
                continue
            hits.append(Hit(matched=m.group(), rule=rule))
    hits.sort(key=lambda h: (h.severity != "Strong", h.matched.lower()))
    return hits


def em_dash_count(text: str) -> int:
    """Em dashes are the single most recognisable AI fingerprint in 2026 prose."""
    return text.count("—")


def report(text: str, *, respect_habits: bool = True) -> dict:
    hits = scan(text, respect_habits=respect_habits)
    return {
        "strong": [str(h) for h in hits if h.severity == "Strong"],
        "medium": [str(h) for h in hits if h.severity != "Strong"],
        "em_dashes": em_dash_count(text),
        "clean": not hits and em_dash_count(text) <= 1,
    }


def advisory_notes(limit: int = 14) -> list[str]:
    """Shape-level tells to hand a reviewing model, since regex cannot see them."""
    return rules()[1][:limit]
