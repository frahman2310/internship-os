"""The learning loop: what the user changes, the system stops doing.

Every save creates a version, so the diff between consecutive versions of a piece IS an
act of correction. This module reads those diffs and turns repeated ones into rules.

Three deliberate design choices:

**Deterministic, not model-inferred.** Asking a model "what does he prefer?" produces
plausible generalisations from one example. Counting which words he actually deletes across
many edits produces a fact. The rules here are arithmetic over diffs, so they are auditable
and cannot drift.

**One correction is enough.** The threshold started at three on the small-n reasoning the
research engine uses, but the user asked for immediate effect: if he bothered to delete
something, the system should stop writing it. The count is still kept, so a repeated
correction still outranks a one-off in the ordering.

**Learned rules are additive to the blacklist, never subtractive.** If the user keeps a word the
blacklist flags, that softens the flag for him only. If he keeps deleting a word the
blacklist permits, it becomes a personal rule. His own corpus already establishes his
habits; this catches what changes as he writes for employers rather than for himself.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

from ..config import REPO_ROOT

LEARNED_PATH = REPO_ROOT / "var" / "voice_learned.json"
MIN_OCCURRENCES = 1          # the user's call: one correction is enough to act on
MIN_PHRASE_LEN = 3           # ignore single-character noise
# Grammatical function words carry no style signal: they appear in almost every deleted
# clause, so without this filter the loop "learns" that the user dislikes the word "about".
# Words that DO carry signal — passionate, really, very, leverage, value — stay learnable.
STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "at", "for", "with",
    "is", "was", "are", "were", "be", "been", "being", "it", "that", "this", "as", "by",
    "from", "i", "my", "me", "we", "our", "you", "your", "he", "she", "they", "his", "her",
    "about", "would", "could", "should", "will", "shall", "have", "has", "had", "do",
    "does", "did", "can", "may", "might", "must", "into", "onto", "over", "under",
    "than", "then", "there", "their", "them", "these", "those", "such", "each", "both",
    "when", "which", "while", "where", "who", "whom", "whose", "what", "since",
    "though", "although", "upon", "after", "before", "during", "through", "between",
    "among", "against", "without", "within", "across", "also", "any", "some", "other",
    "same", "own", "been", "were", "not", "nor", "its", "was",
}


def tokens(text: str) -> list[str]:
    return re.findall(r"[A-Za-z][A-Za-z'-]*", text or "")


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text or "") if s.strip()]


@dataclass
class EditDiff:
    removed: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    words_before: int = 0
    words_after: int = 0
    sentences_before: int = 0
    sentences_after: int = 0

    @property
    def length_delta(self) -> int:
        return self.words_after - self.words_before

    @property
    def mean_sentence_before(self) -> float:
        return round(self.words_before / self.sentences_before, 1) if self.sentences_before else 0.0

    @property
    def mean_sentence_after(self) -> float:
        return round(self.words_after / self.sentences_after, 1) if self.sentences_after else 0.0


def diff(before: str, after: str) -> EditDiff:
    """Word-level diff. Contiguous runs are kept together so 'in order to' is learned as a
    phrase rather than three unrelated deletions."""
    a, b = tokens(before), tokens(after)
    out = EditDiff(
        words_before=len(a), words_after=len(b),
        sentences_before=len(sentences(before)), sentences_after=len(sentences(after)),
    )
    for tag, i1, i2, j1, j2 in SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if tag in ("delete", "replace"):
            run = " ".join(a[i1:i2]).strip()
            if len(run) >= MIN_PHRASE_LEN:
                out.removed.append(run)
        if tag in ("insert", "replace"):
            run = " ".join(b[j1:j2]).strip()
            if len(run) >= MIN_PHRASE_LEN:
                out.added.append(run)
    return out


MAX_LEXICAL_RUN = 25   # beyond this a deletion is structural, not a word choice


def _significant(run: str) -> set[str]:
    """Learnable units in one edited run.

    A short run is learnable as a whole phrase ("in order to"). A longer one is not — but
    its content words still are, which is the case that matters: deleting the sentence
    "I am very passionate about finance" three times is exactly how the system should
    learn that "passionate" is unwelcome. Only a genuinely paragraph-sized deletion is
    ignored, because that is a structural cut and teaches nothing about vocabulary.

    Returns a SET: a word repeated inside one edit is still one piece of evidence, so the
    occurrence threshold counts separate edits rather than repetitions.
    """
    parts = run.lower().split()
    if not parts or len(parts) > MAX_LEXICAL_RUN:
        return set()
    units = {run.lower()} if 1 < len(parts) <= 6 else set()
    units |= {p.strip(".,;:!?") for p in parts
              if p not in STOPWORDS and len(p.strip(".,;:!?")) > 3}
    return {u for u in units if u}


@dataclass
class Learned:
    dislikes: dict[str, int] = field(default_factory=dict)   # he removes these
    prefers: dict[str, int] = field(default_factory=dict)    # he adds these
    kept_despite_flag: dict[str, int] = field(default_factory=dict)
    edits_seen: int = 0
    mean_length_delta: float = 0.0

    def to_json(self) -> dict:
        return {
            "dislikes": self.dislikes, "prefers": self.prefers,
            "kept_despite_flag": self.kept_despite_flag,
            "edits_seen": self.edits_seen,
            "mean_length_delta": self.mean_length_delta,
        }

    @staticmethod
    def from_json(d: dict) -> "Learned":
        return Learned(
            dislikes=d.get("dislikes", {}), prefers=d.get("prefers", {}),
            kept_despite_flag=d.get("kept_despite_flag", {}),
            edits_seen=d.get("edits_seen", 0),
            mean_length_delta=d.get("mean_length_delta", 0.0),
        )

    @property
    def rules(self) -> list[str]:
        """Rules that have cleared the repetition threshold, strongest first.

        A word he has both removed and added, across different edits, is a change of mind
        (Sir/Madam, then Hiring Team, then Sir/Madam again), not a preference either way.
        """
        both = set(self.dislikes) & set(self.prefers)
        out = [(n, f"never write {w!r} — removed {n} times")
               for w, n in self.dislikes.items() if n >= MIN_OCCURRENCES and w not in both]
        out += [(n, f"prefer {w!r} — added {n} times")
                for w, n in self.prefers.items() if n >= MIN_OCCURRENCES and w not in both]
        if self.edits_seen >= MIN_OCCURRENCES and abs(self.mean_length_delta) >= 8:
            verb = "cuts" if self.mean_length_delta < 0 else "expands"
            out.append((99, f"he {verb} drafts by ~{abs(self.mean_length_delta):.0f} words on average — "
                            f"draft {'shorter' if self.mean_length_delta < 0 else 'fuller'}"))
        return [text for _, text in sorted(out, key=lambda x: -x[0])]


def load() -> Learned:
    if not LEARNED_PATH.exists():
        return Learned()
    try:
        return Learned.from_json(json.loads(LEARNED_PATH.read_text()))
    except (ValueError, OSError):
        return Learned()


def save(learned: Learned) -> None:
    LEARNED_PATH.parent.mkdir(parents=True, exist_ok=True)
    LEARNED_PATH.write_text(json.dumps(learned.to_json(), indent=2, ensure_ascii=False))


def record(before: str, after: str) -> Learned:
    """Fold one edit into the learned model and persist it."""
    from .voice import scan

    d = diff(before, after)
    learned = load()

    rem, add = Counter(), Counter()
    for run in d.removed:
        rem.update(_significant(run))      # set per run: no double-counting inside one edit
    for run in d.added:
        add.update(_significant(run))
    # A word he both removed and added in the same edit is a reshuffle, not a preference.
    for w in set(rem) & set(add):
        rem.pop(w, None)
        add.pop(w, None)

    for w, n in rem.items():
        learned.dislikes[w] = learned.dislikes.get(w, 0) + n
    for w, n in add.items():
        learned.prefers[w] = learned.prefers.get(w, 0) + n

    # Anything the scanner flagged that survived his edit is a term he is happy to own.
    surviving = {h.matched.lower() for h in scan(after, respect_habits=False)}
    for w in surviving:
        learned.kept_despite_flag[w] = learned.kept_despite_flag.get(w, 0) + 1

    total_before = learned.mean_length_delta * learned.edits_seen
    learned.edits_seen += 1
    learned.mean_length_delta = round((total_before + d.length_delta) / learned.edits_seen, 1)

    save(learned)
    return learned


def guidance(limit: int = 12) -> str:
    """Learned rules as a prompt fragment, for the drafting agents."""
    rules = load().rules[:limit]
    if not rules:
        return ""
    return ("Learned from the user's own edits to previous drafts — these override general "
            "style advice:\n  - " + "\n  - ".join(rules))


def softened_flags(threshold: int = 2) -> set[str]:
    """Terms the scanner flags but the user has repeatedly chosen to keep."""
    learned = load()
    return {w for w, n in learned.kept_despite_flag.items() if n >= threshold}
