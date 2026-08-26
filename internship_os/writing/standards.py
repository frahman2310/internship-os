"""The benchmark: what strong finance and tech application writing actually does.

Derived from published guidance and real annotated examples (Oxford and MIT careers
services, Mergers & Inquisitions, eFinancialCareers, 10X EBITDA, targetjobs, and a
hiring manager's public critique on freeCodeCamp), plus recruiter statements about what
now reads as machine-written.

Ranked BELOW the user's own notes and below what his edits demonstrate. A benchmark should
inform the writing; it must never outrank an instruction from the person whose name is on
the letter.

Most of this is prose guidance, but the parts that can be checked mechanically are, because
a rule that is only in a prompt is a rule that gets ignored under pressure. The sharpest is
the **swap test**: if the letter still reads correctly with another firm's name in it, the
letter has not done its job.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Hard word ceilings. One page is the universal cap; finance guidance runs tighter still,
# recommending under 300 words for an IB letter.
MAX_WORDS_FINANCE = 400
IDEAL_WORDS_FINANCE = 300
MAX_WORDS_TECH = 500

STANDARDS = """
Benchmark from strong published examples and recruiter guidance (finance and tech):
  * Never open with "I am writing to apply for..." — it spends the first sentence on
    something the reader already knows. Open on a specific thing you did, saw or made.
  * The swap test: if this letter would still read correctly with a different firm's name
    in it, it has failed. Every letter needs at least one fact true of this employer alone.
  * Cite a verifiable firm artefact — a named deal, a published case study, a stated
    policy — and then link it to something you actually did. Never recite the firm's own
    website statistics back at it, and never call a firm "the best".
  * The letter is not a CV summary. It adds the context the CV cannot carry.
  * Replace adjective self-description with one named completed action. "Flexible problem
    solver" is the failure mode; a migration you shipped is the fix.
  * Do not start consecutive sentences with "I".
  * Close on a forward action, not "I look forward to your response".
  * Avoid motivations that would fit any job: fast-paced environment, learn a lot,
    well-rounded skill set.
  * Recruiters now read flawless grammar plus generic content as machine-written. What
    reads as human is specific, slightly untidy detail: named companies, real numbers,
    the thing that went wrong.
"""


@dataclass
class Finding:
    rule: str
    detail: str
    severity: str = "fail"   # fail | warn


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text or "") if s.strip()]


def words(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", text or ""))


BANNED_OPENERS = re.compile(
    r"^\s*(i am writing to (apply|express)|i would like to apply|i wish to apply|"
    r"please (accept|find) (this|my)|i am excited to apply)", re.I)
PASSIVE_CLOSE = re.compile(
    r"(look forward to (your|hearing)|await your (response|reply)|"
    r"hope to hear from you|thank you for your (time and )?consideration\s*\.?\s*$)", re.I)
FLATTERY = re.compile(
    r"\b(the best (bank|firm|adviser|team)|world[- ]class|prestigious|leading light|"
    r"unrivalled reputation|second to none)\b", re.I)
GENERIC_MOTIVE = re.compile(
    r"\b(fast[- ]paced environment|learn a lot|well[- ]rounded skill|steep learning curve|"
    r"gain exposure to|broaden my horizons)\b", re.I)


def check(text: str, *, company: str = "", track: str = "finance") -> list[Finding]:
    """Deterministic checks against the benchmark. Cheap, auditable, and run before the user
    sees a draft."""
    out: list[Finding] = []
    n = words(text)
    cap = MAX_WORDS_FINANCE if track == "finance" else MAX_WORDS_TECH

    if n > cap:
        out.append(Finding("length", f"{n} words against a {cap} ceiling for {track}"))
    elif track == "finance" and n > IDEAL_WORDS_FINANCE:
        out.append(Finding(
            "length", f"{n} words — finance guidance recommends under {IDEAL_WORDS_FINANCE}",
            "warn"))

    if BANNED_OPENERS.search(text):
        out.append(Finding("opener", "opens with the canonical stop-reading line"))

    # The swap test, mechanised: a letter with no mention of the employer is generic by
    # construction. Naming them once is necessary, not sufficient.
    if company:
        stem = re.split(r"\s*(?:&|and)\s*", company)[0].strip().rstrip(".,")
        if stem and stem.lower() not in text.lower():
            out.append(Finding("swap_test", f"never names {company} — would suit any firm"))
        elif len(re.findall(re.escape(stem), text, re.I)) < 2:
            out.append(Finding(
                "swap_test", f"names {stem} only once — check a firm-specific fact carries it",
                "warn"))

    if PASSIVE_CLOSE.search(text):
        out.append(Finding("close", "closes passively rather than on a forward action"))
    if m := FLATTERY.search(text):
        out.append(Finding("flattery", f"flattery with no information content: {m.group()!r}"))
    if m := GENERIC_MOTIVE.search(text):
        out.append(Finding("generic_motive", f"motivation that fits any job: {m.group()!r}"))

    # Consecutive sentences opening on "I" is the most-cited student-letter tell.
    sents = _sentences(text)
    runs, run = 0, 0
    for s in sents:
        if re.match(r"^I\b", s):
            run += 1
            runs = max(runs, run)
        else:
            run = 0
    if runs >= 3:
        out.append(Finding("i_openings", f"{runs} consecutive sentences begin with 'I'"))
    elif runs == 2:
        out.append(Finding("i_openings", "two consecutive sentences begin with 'I'", "warn"))

    # Numbers are the cheapest signal of a human account.
    if not re.search(r"\d", text):
        out.append(Finding("specificity", "contains no numbers at all", "warn"))
    return out


def report(text: str, *, company: str = "", track: str = "finance") -> dict:
    findings = check(text, company=company, track=track)
    return {
        "passed": not [f for f in findings if f.severity == "fail"],
        "findings": [{"rule": f.rule, "detail": f.detail, "severity": f.severity}
                     for f in findings],
        "words": words(text),
    }
