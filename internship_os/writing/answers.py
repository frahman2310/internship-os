"""Short free-text application answers — the third writing surface, kept separate from
the CV and the cover letter because it obeys different rules.

A CV is selected evidence. A cover letter is one continuous argument. An application
answer is neither: it is a constrained response to somebody else's question, usually with
a hard word or character limit, and usually scored against a competency rubric the
employer will not show you.

So this module does three things the other two do not:

  * **Enforces the limit as a hard constraint**, checked after generation. An answer over
    the limit is not "slightly long" — most portals silently truncate it mid-sentence.
  * **Names the competency being tested**, so a "describe a time you..." answer is built
    as situation → action → outcome rather than as prose.
  * **Runs the compiled AI-tell scanner** before you ever see the draft, and revises once.

Grounding is inherited unchanged: cite evidence ids, or the paragraph is ungrounded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..documents.cover import audit, numbers_in
from ..documents.plan import score_evidence, tokenize
from ..llm.schema_call import call_schema
from ..models.candidate import Candidate, EvidenceItem
from ..models.company import Company
from ..models.opportunity import Opportunity
from ..models.research import ResearchClaim
from .notes import combined_guidance
from .voice import advisory_notes, report

MAX_EVIDENCE = 24
MAX_CLAIMS = 10

# What a question is really asking. Employers score against a rubric; answering the
# literal words while missing the competency is the most common way to lose marks.
COMPETENCIES = {
    "why_firm": "Genuine, specific motivation — evidence you know what this firm actually does.",
    "why_role": "Understanding of the day-to-day work, not the glamour of the industry.",
    "teamwork": "Your specific contribution within a group, not the group's achievement.",
    "challenge": "Situation, the action YOU took, the measurable outcome, what changed after.",
    "leadership": "Decision-making under constraint and responsibility for the result.",
    "failure": "Honest ownership plus the concrete behaviour change that followed.",
    "commercial": "Awareness of how the firm makes money and what moves its market.",
    "strength": "One claim, evidenced with a real example, not a list of adjectives.",
    "other": "Answer the literal question, with a concrete example carrying the weight.",
}


def classify(question: str) -> str:
    q = question.lower()
    pairs = [
        ("why_firm", ("why do you want to work", "why do you want to join", "why us",
                      "why this firm", "why our", "about us", "why have you applied")),
        ("why_role", ("why this role", "why this programme", "why this scheme",
                      "why division", "which division", "why did you choose this")),
        ("teamwork", ("team", "collaborat", "group", "with others", "alongside others")),
        ("failure", ("fail", "mistake", "went wrong", "setback")),
        ("challenge", ("challenge", "difficult", "problem you", "obstacle", "overcame")),
        ("leadership", ("lead", "led a", "responsib", "initiative")),
        ("commercial", ("commercial", "market", "industry trend", "recent deal", "news")),
        ("strength", ("strength", "what makes you", "why should we", "stand out")),
    ]
    for key, needles in pairs:
        if any(n in q for n in needles):
            return key
    # A bare "Why <Firm>?" is the commonest form of the motivation question and matches
    # none of the phrasings above. Any remaining question opening on "why" is about
    # motivation, so treat it as such rather than dropping it into the generic bucket.
    if q.lstrip().startswith("why"):
        return "why_firm"
    return "other"


def word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", text))


class Draft(BaseModel):
    text: str = Field(description="The answer, within the stated limit")
    evidence_ids: list[int] = Field(default_factory=list)
    claim_ids: list[int] = Field(default_factory=list)


@dataclass
class Answer:
    question: str
    competency: str
    text: str
    words: int
    limit: int | None
    grounding: str
    audit_note: str
    tells: dict = field(default_factory=dict)

    @property
    def within_limit(self) -> bool:
        return self.limit is None or self.words <= self.limit

    @property
    def ok(self) -> bool:
        return self.within_limit and self.grounding != "ungrounded" and self.tells.get("clean", False)


async def _context(session: AsyncSession, opportunity_id: int):
    opp = await session.get(Opportunity, opportunity_id)
    if opp is None:
        raise ValueError(f"opportunity {opportunity_id} not found")
    company = await session.get(Company, opp.company_id)
    candidate = (await session.execute(select(Candidate))).scalars().first()
    if candidate is None:
        raise ValueError("no candidate imported — run `profile import` first")
    return opp, company, candidate


async def build_answer(
    session: AsyncSession,
    opportunity_id: int,
    question: str,
    *,
    word_limit: int | None = 250,
    document_id: int | None = None,
) -> Answer:
    """`document_id` scopes the guidance: notes the user wrote on THIS piece apply alongside
    his global ones, so a correction made here is not lost the next time it is redrafted."""
    opp, company, candidate = await _context(session, opportunity_id)
    competency = classify(question)
    terms = tokenize(f"{question} {opp.title} {opp.industry or ''}")

    items = (await session.execute(
        select(EvidenceItem).where(
            EvidenceItem.candidate_id == candidate.id, EvidenceItem.is_active.is_(True)
        )
    )).scalars().all()
    items = sorted(items, key=lambda i: -score_evidence(i, set(), terms))[:MAX_EVIDENCE]
    evidence = {i.id: i.text for i in items}

    claim_rows = (await session.execute(
        select(ResearchClaim).where(
            ResearchClaim.subject_id == opportunity_id,
            ResearchClaim.superseded_by_id.is_(None),
            ResearchClaim.confidence >= 0.5,
        ).order_by(ResearchClaim.confidence.desc())
    )).scalars().all()[:MAX_CLAIMS]
    claims = {c.id: c.statement for c in claim_rows}

    ev_block = "\n".join(f"[{i}] {t}" for i, t in evidence.items()) or "(none)"
    cl_block = "\n".join(f"[{c.id}] {c.statement}" for c in claim_rows) or (
        "(no verified research on this employer — do NOT invent a reason to admire them; "
        "write about the work itself)"
    )
    limit_line = (
        f"HARD LIMIT: {word_limit} words. Going over means the portal truncates you mid-sentence."
        if word_limit else "No stated limit; keep it under 250 words."
    )

    prompt = (
        f"Applicant: {candidate.full_name}\n"
        f"Employer: {company.name if company else ''} — {opp.title}\n\n"
        f"QUESTION: {question}\n\n"
        f"What this question is really testing: {COMPETENCIES[competency]}\n\n"
        f"{limit_line}\n\n"
        f"EVIDENCE you may use (cite ids in evidence_ids):\n{ev_block}\n\n"
        f"RESEARCH on this employer (cite ids in claim_ids):\n{cl_block}\n\n"
        f"{combined_guidance(document_id)}\n"
        "Avoid these shapes, which read as machine-written:\n  - "
        + "\n  - ".join(advisory_notes(10))
        + "\n\nUse only what is above. State no number that is not in what you cite. "
        "Lead with the substance — no throat-clearing opener restating the question."
    )

    drafted = await call_schema(
        prompt, Draft, system="You write UK internship application answers.",
        purpose="writing:answer", temperature=0.4, max_tokens=2600,
    )
    text = drafted.text.strip()
    tells = report(text)

    # One revision pass, and only when there is something concrete to fix. Looping here
    # spends tokens re-rolling the same prose.
    over = word_limit is not None and word_count(text) > word_limit
    if tells["strong"] or over or tells["em_dashes"] > 1:
        problems = []
        if tells["strong"]:
            problems.append("Remove these AI tells, keeping the meaning: " + "; ".join(tells["strong"]))
        if tells["em_dashes"] > 1:
            problems.append(f"{tells['em_dashes']} em dashes — replace all but at most one with commas or full stops.")
        if over:
            problems.append(f"{word_count(text)} words against a {word_limit} limit — cut, do not summarise.")
        revised = await call_schema(
            f"Revise this answer.\n\n{text}\n\nFix:\n- " + "\n- ".join(problems)
            + "\n\nChange nothing factual. Keep the same evidence ids.",
            Draft, system="You edit application answers.", purpose="writing:answer:revise",
            temperature=0.2, max_tokens=2600,
        )
        if revised.text.strip():
            drafted = Draft(
                text=revised.text.strip(),
                evidence_ids=drafted.evidence_ids,
                claim_ids=drafted.claim_ids,
            )
            text = drafted.text
            tells = report(text)

    verdict = audit(drafted, evidence, claims, context=f"{opp.title} {company.name if company else ''}")
    return Answer(
        question=question,
        competency=competency,
        text=text,
        words=word_count(text),
        limit=word_limit,
        grounding=verdict.grounding,
        audit_note=verdict.note,
        tells=tells,
    )
