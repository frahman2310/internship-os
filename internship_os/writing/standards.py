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
  * For free-text ANSWERS, close on a forward action. (Cover LETTERS follow Morgan
    Stanley's guide instead and do thank the reader — see the letter form.)
  * Avoid motivations that would fit any job: fast-paced environment, learn a lot,
    well-rounded skill set.
  * Recruiters now read flawless grammar plus generic content as machine-written. What
    reads as human is specific, slightly untidy detail: named companies, real numbers,
    the thing that went wrong.
"""


# Morgan Stanley's own published cover-letter guide, which the user has asked to follow
# exactly. Where it conflicts with the general benchmark it wins: it is the employer's
# stated preference, and the general benchmark is aggregated advice about other employers.
MORGAN_STANLEY_FORM = """
Cover letters must be written as LETTERS, following Morgan Stanley's published guide:
  * Your name and contact details at the top, then the date, then the firm's name and
    address, then a salutation — a named person if known, otherwise "Dear Sir/Madam" or
    "Dear Hiring Team".
  * FIRST paragraph: state the position applied for, and how you became aware of it if
    that was a recruiting event.
  * MIDDLE, two or three paragraphs: what attracted you to this type of work; why this
    firm specifically; why you would fit.
  * LAST paragraph: thank the employer and say you look forward to a response.
  * Close with "Yours sincerely," (named person) or "Yours faithfully," (Dear Sir/Madam),
    then your name.
  * Do NOT repeat skills already covered in the CV against the job description. DO include
    relevant experience the job description did not ask for — leadership, collaboration or
    problem-solving from extracurriculars — provided it is genuinely relevant, not a stretch.
  * Never reuse a generic letter. A wrong firm name is a deal-breaker.
"""

# Under Morgan Stanley's guide a courteous close is REQUIRED, so the general benchmark's
# "passive close is weak" rule must not fire in letter mode. Two credible sources disagree
# here; the employer's own instruction governs a letter addressed to that employer.
LETTER_MODE_EXEMPT = {"close"}


# the user's own standing rules, given as corrections on real drafts. These are permanent and
# outrank every other source here, so they live in code rather than only in the notes file:
# a rule that can be deleted by clearing a JSON file is not a rule.
#
# Calibration note: his own formal writing averages 23.1 words per sentence. The answers
# he called "small awkward sentences" measured 16.4. That gap is what the flow check below
# is measuring, rather than a stylistic opinion.
HOUSE_RULES = """
The user's standing rules. These are absolute and override every other instruction here:
  * NEVER use an em dash or an en dash. Use a comma. This has no exceptions.
  * Write in flowing paragraphs. No bold sub-headings inside an answer, no fragment lists,
    no one-line paragraphs. Sentences should run long and connect to each other; his own
    formal writing averages about 23 words a sentence and the prose should sit near that.
  * Never comment on your own structure. "Three, in the order I would defend them" and
    anything like it is banned.
  * Never hedge or pre-concede. "though I know placement follows selection and I would take
    any of the four" is exactly the move to avoid. State the preference and stop.
  * Do not restate the CV. The reader has it. Add what it cannot carry.
  * These are business answers. Be clear, be concrete, and do not be dramatic. Keep some
    feeling, but far less than a personal essay would carry.
  * Answer the question that was asked, completely and directly.
  * No decorative white space, no gratuitous line breaks inside an answer.
  * A project may be the CRUX of only ONE answer per firm. It may still appear in one
    line elsewhere as supporting evidence, but never carry two answers. If Orderly is the
    project answer, the product answer runs on something else. Spread the evidence across
    a firm: Orderly, AgentF, FaizOS, the Sales Intelligence Pipeline, Personal University,
    AgentNotify, the payment-token work, Lakson, Grant Thornton, Arif Habib.
  * PREFER THE PORTFOLIO OVER THE CV. Employers already hold the CV, so repeating it
    spends the answer on what they have read. They have never seen the portfolio: AgentF's
    funding, Orderly's architecture, the sales pipeline's 758 leads and 614 drafts,
    FaizOS's 38 builds, Personal University shipped as one 470KB offline file with a
    lossless SHA-256 rebuild and 15 of 15 Playwright tests, AgentNotify, the NFC token
    work. Lead with those and use CV lines to corroborate, not the reverse.
  * Match the weight of the evidence to the weight of the question. An achievement or
    "proudest moment" question needs a built, funded or shipped thing, never an
    investment note or a piece of coursework.
  * A non-academic achievement is fine, and often stronger, but it must be tied back to
    academic work, professional experience or a transferable skill. Never leave it as a
    standalone anecdote.
  * In FINANCE answers, lead with the AI and FinTech ventures wherever they are the
    stronger evidence. AgentF is funded, Orderly has paying venues, the token work is a
    real payments product. Those beat a shadowing week or a written note.
"""

# Rules that can be checked mechanically. Everything above that cannot be is prompt text.
DASHES = re.compile(r"[\u2013\u2014]")
META_STRUCTURE = re.compile(
    r"(?i)(\b(three|two|four)\s*,\s*in (the )?order\b|in the order I would|"
    r"\bfirst(ly)?,? .{0,40}\bsecond(ly)?\b.{0,40}\bthird(ly)?\b|"
    r"\bin this (answer|letter) I will\b|\bI will now\b|\bto summarise\b)")
HEDGE = re.compile(
    r"(?i)(though I know|although I know|I would take any|I would be (glad|happy) of any|"
    r"I appreciate that I|I realise (that )?I (only|just)|I am aware (that )?I lack|"
    r"despite (my )?limited|even though I have (only|just))")
BOLD_HEADING = re.compile(r"^\s*\*\*[^*\n]{2,80}\*\*", re.M)  # bold used as a run-in heading
MIN_MEAN_SENTENCE = 18.0     # his formal register sits at 23.1; below 18 reads as choppy
MAX_MEAN_SENTENCE = 30.0     # and above 30 it stops being readable, which is the failure
                             # mode of over-correcting the choppiness complaint


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


# The swap test asks whether a piece could be sent to a different firm. That is the right
# question for a cover letter or a "why us" answer, and the wrong one for "describe a time
# you worked in a team" — a competency answer is about the candidate, and naming the
# employer inside it would read as forced. Applying the test everywhere produced four false
# failures on the user's Bank of America answers.
# Commercial-awareness questions were originally in this set and should not be. "Name a
# news item that has recently driven the financial markets" and "describe a technology
# trend you have followed" are about the world, not the employer, and forcing the firm's
# name into them produces exactly the contrived sentence the swap test exists to prevent.
FIRM_SPECIFIC_COMPETENCIES = {"why_firm", "why_role", "cover_letter"}


def swap_test_applies(competency: str) -> bool:
    return competency in FIRM_SPECIFIC_COMPETENCIES


def check(text: str, *, company: str = "", track: str = "finance",
          letter: bool = False, competency: str = "cover_letter") -> list[Finding]:
    """Deterministic checks against the benchmark. Cheap, auditable, and run before the user
    sees a draft.

    `letter=True` applies Morgan Stanley's form: the courteous close it mandates stops
    being a finding, and the letter's structural furniture is checked instead.
    """
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
    if company and swap_test_applies(competency):
        # Match the distinctive first word, not the full legal name. An answer that says
        # "Qatalyst" is naming the firm; requiring "Qatalyst Partners" flagged three
        # correctly-written answers as generic. Suffixes like Partners, Capital, Group and
        # Management are shared across firms and carry no identifying force.
        SUFFIXES = {"partners", "capital", "group", "management", "co", "co.", "llp",
                    "ltd", "limited", "plc", "inc", "international", "securities",
                    "investments", "investment", "trading", "sachs", "holdings"}
        parts = [w.strip(".,&") for w in re.split(r"\s+", company) if w.strip(".,&")]
        stem = next((w for w in parts if w.lower() not in SUFFIXES), parts[0] if parts else "")
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

    # --- the user's standing rules, checked mechanically --------------------------------
    if m := DASHES.search(text):
        n_dash = len(DASHES.findall(text))
        out.append(Finding("dashes", f"{n_dash} dash(es) — he has banned these, use commas"))
    if m := META_STRUCTURE.search(text):
        out.append(Finding("meta_structure", f"comments on its own structure: {m.group()[:40]!r}"))
    if m := HEDGE.search(text):
        out.append(Finding("hedging", f"pre-concedes: {m.group()[:48]!r}"))
    if BOLD_HEADING.search(text):
        out.append(Finding("fragmented", "bold sub-headings inside an answer; use flowing paragraphs"))
    sents = _sentences(text)
    if len(sents) >= 6:
        mean = sum(len(s.split()) for s in sents) / len(sents)
        if mean < MIN_MEAN_SENTENCE:
            out.append(Finding(
                "flow", f"mean sentence {mean:.1f} words; his own formal register is 23.1, "
                        "so this reads choppy"))
        elif mean > MAX_MEAN_SENTENCE:
            out.append(Finding(
                "flow", f"mean sentence {mean:.1f} words; over 30 stops being readable, "
                        "aim near his 23.1"))

    if letter:
        low = text.lower()
        if not re.search(r"\bdear\b", low):
            out.append(Finding("letter_form", "no salutation — needs 'Dear ...'"))
        if not re.search(r"yours (sincerely|faithfully)|kind regards|sincerely,", low):
            out.append(Finding("letter_form", "no sign-off — needs 'Yours sincerely/faithfully,'"))
        if not re.search(r"\bthank you\b|\bthank\b", low):
            out.append(Finding("letter_form",
                               "Morgan Stanley's guide requires thanking the employer", "warn"))
        out = [f for f in out if f.rule not in LETTER_MODE_EXEMPT]
    return out


def report(text: str, *, company: str = "", track: str = "finance",
           letter: bool = False, competency: str = "cover_letter") -> dict:
    findings = check(text, company=company, track=track, letter=letter,
                     competency=competency)
    return {
        "passed": not [f for f in findings if f.severity == "fail"],
        "findings": [{"rule": f.rule, "detail": f.detail, "severity": f.severity}
                     for f in findings],
        "words": words(text),
    }


# Evidence the user can draw on. The repetition check is cross-piece, so it cannot live in
# check(), which only ever sees one answer at a time. Repeating a project inside one
# firm's set is the failure it exists to catch.
EVIDENCE_MARKERS = {
    "Orderly": r"\bOrderly\b",
    "AgentF": r"\bAgentF\b",
    "FaizOS": r"\bFaizOS\b",
    "AgentNotify": r"\bAgentNotify\b",
    "Personal University": r"Personal University",
    "Sales Intelligence Pipeline": r"[Ss]ales [Ii]ntelligence|outreach engine|\b758\b|\b614\b",
    "Payment tokens": r"NFC|payment token|single[- ]use token",
    "Lakson": r"\bLakson\b",
    "Grant Thornton Pakistan": r"Grant Thornton Pakistan|Alfan|Fatima Group",
    "Grant Thornton UK": r"Debt Advisory|SONIA",
    "Arif Habib": r"Arif Habib|Dolmen",
    "Track team": r"track team|under[- ]17|900 days",
    "UJALA": r"UJALA",
    "Event management": r"ticketed events|\$8,000",
}


def projects_used(text: str) -> set[str]:
    return {name for name, pat in EVIDENCE_MARKERS.items()
            if re.search(pat, text or "")}


def load_bearing(text: str) -> set[str]:
    """Projects an answer actually rests on, as opposed to name-checks.

    the user's rule: a project may appear in one line of another answer as supporting
    evidence, but it cannot be the crux of two answers for the same firm. A single
    mention is a reference; two or more means the answer is built on it.
    """
    out = set()
    for name, pat in EVIDENCE_MARKERS.items():
        if len(re.findall(pat, text or "")) >= 2:
            out.add(name)
    return out


def repeated_projects(pieces: dict[str, str]) -> dict[str, list[str]]:
    """Evidence that is load-bearing in more than one of a firm's pieces.

    `pieces` maps a label to its text. Empty dict means the set is clean. Passing
    mentions are deliberately not flagged.
    """
    seen: dict[str, list[str]] = {}
    for label, text in pieces.items():
        for name in load_bearing(text):
            seen.setdefault(name, []).append(label)
    return {k: v for k, v in seen.items() if len(v) > 1}
