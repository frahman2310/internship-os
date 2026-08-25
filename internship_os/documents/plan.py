"""Choose what goes on the CV for one opportunity. Deterministic — no LLM.

Selection, not generation, is what makes the grounding audit trivially pass: every bullet
that reaches the page IS an `evidence_items` row, carried through by id. There is no step
at which prose is invented, so there is nothing for a fabrication check to catch later.

Ranking is keyword overlap between the opportunity (title, industry, track, categories)
and each evidence item's own text plus its linked skills. Crude on purpose: a tie broken
slightly wrong costs a slightly weaker bullet, while an LLM here would cost groundedness.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.candidate import Candidate, CandidateLanguage, EvidenceItem, EvidenceSkill, Experience, Skill
from ..models.company import Company
from ..models.opportunity import Opportunity

# Rough one-page budget for uk-1page at 10pt. Overflow is caught for real by the page
# count after compiling; these caps just make overflow rare.
MAX_WORK = 4
MAX_BULLETS_PER_WORK = 3
MAX_PROJECTS = 2
MAX_BULLETS_PER_PROJECT = 2

FINANCE_TERMS = {
    "finance", "investment", "banking", "equity", "credit", "advisory", "m&a", "markets",
    "valuation", "portfolio", "asset", "research", "trading", "risk", "capital", "reit",
    "dcf", "lbo", "ebitda", "financial", "economics",
}
TECH_TERMS = {
    "software", "engineering", "developer", "python", "backend", "frontend", "data",
    "machine", "learning", "ai", "ml", "infrastructure", "api", "systems", "platform",
    "sql", "typescript", "react", "cloud", "pipeline", "automation",
}
QUANT_TERMS = {
    "quant", "quantitative", "mathematics", "statistics", "stochastic", "probability",
    "modelling", "modeling", "numerical", "algorithm", "derivatives", "pricing",
}

STOPWORDS = {
    "the", "and", "for", "with", "a", "an", "of", "to", "in", "on", "at", "by", "from",
    "summer", "internship", "intern", "programme", "program", "2027", "2026", "uk",
    "london", "analyst", "student", "placement", "scheme",
}


def tokenize(text: str | None) -> set[str]:
    if not text:
        return set()
    return {t for t in re.findall(r"[a-z0-9&+#]+", text.lower()) if t not in STOPWORDS and len(t) > 1}


def is_clean_skill(name: str) -> bool:
    """Reject fragments produced by splitting a skill list on commas that sat inside
    brackets — 'FX)' and 'macro analysis (inflation' are halves of one real skill.
    Printing them verbatim looks like an unproofread CV, which is worse than omitting."""
    name = name.strip()
    if not name or name.count("(") != name.count(")"):
        return False
    if name[0] in "-—–,:;)":  # a trailing clause that lost its head
        return False
    return not name.rstrip().endswith((":", ",", "-"))


@dataclass
class Bullet:
    text: str
    evidence_item_id: int
    score: float


@dataclass
class Entry:
    organisation: str
    title: str | None
    location: str | None
    start_date: str | None
    end_date: str | None
    bullets: list[Bullet] = field(default_factory=list)


@dataclass
class ContentPlan:
    candidate: Candidate
    opportunity: Opportunity
    company_name: str
    track: str
    education: list[Entry]
    work: list[Entry]
    projects: list[Entry]
    skills_line: str
    languages_line: str
    interests_line: str | None
    dropped: list[str] = field(default_factory=list)

    def evidence_ids(self) -> list[int]:
        ids = []
        for entry in [*self.education, *self.work, *self.projects]:
            ids.extend(b.evidence_item_id for b in entry.bullets)
        return ids


def infer_track(opp: Opportunity) -> str:
    """finance | tech. The opportunity's own `track` wins when it is decisive."""
    if opp.track in {"finance", "tech"}:
        return opp.track
    blob = tokenize(" ".join(filter(None, [opp.title, opp.industry, " ".join(opp.categories or [])])))
    return "tech" if len(blob & TECH_TERMS) > len(blob & FINANCE_TERMS) else "finance"


def target_terms(opp: Opportunity, track: str) -> set[str]:
    terms = tokenize(" ".join(filter(None, [
        opp.title, opp.industry, opp.programme_type, " ".join(opp.categories or []),
    ])))
    terms |= TECH_TERMS if track == "tech" else FINANCE_TERMS
    if opp.is_quant:
        terms |= QUANT_TERMS
    return terms


def score_evidence(item: EvidenceItem, skill_names: set[str], terms: set[str]) -> float:
    """Overlap with the target, nudged by category. Metrics win ties: a bullet with a
    number in it outperforms an adjective on every CV ever read by a human."""
    tokens = tokenize(item.text) | skill_names
    overlap = len(tokens & terms)
    # Weighted well above the bonuses below: a bullet that is actually about the target
    # domain must beat an off-topic bullet that merely carries a number.
    score = 2.0 * overlap
    if item.metric_value is not None:
        score += 0.75
    if item.category == "achievement":
        score += 0.75
    elif item.category in {"skill", "tool"}:
        score += 0.25
    return score


async def build_plan(session: AsyncSession, opportunity_id: int) -> ContentPlan:
    opp = await session.get(Opportunity, opportunity_id)
    if opp is None:
        raise ValueError(f"opportunity {opportunity_id} not found")
    company = await session.get(Company, opp.company_id)
    candidate = (await session.execute(select(Candidate))).scalars().first()
    if candidate is None:
        raise ValueError("no candidate imported — run `profile import` first")

    track = infer_track(opp)
    terms = target_terms(opp, track)

    experiences = (
        (await session.execute(
            select(Experience).where(Experience.candidate_id == candidate.id)
        )).scalars().all()
    )
    evidence = (
        (await session.execute(
            select(EvidenceItem).where(
                EvidenceItem.candidate_id == candidate.id, EvidenceItem.is_active.is_(True)
            )
        )).scalars().all()
    )
    # skill names per evidence item, so "Python" on a linked skill counts even when the
    # bullet text never says it
    skill_rows = (await session.execute(
        select(EvidenceSkill.evidence_item_id, Skill.canonical_name).join(
            Skill, Skill.id == EvidenceSkill.skill_id
        )
    )).all()
    skills_by_evidence: dict[int, set[str]] = {}
    for ev_id, name in skill_rows:
        skills_by_evidence.setdefault(ev_id, set()).add(name.lower())

    by_experience: dict[int | None, list[EvidenceItem]] = {}
    for item in evidence:
        by_experience.setdefault(item.experience_id, []).append(item)

    dropped: list[str] = []

    def make_entry(exp: Experience, max_bullets: int) -> Entry:
        items = by_experience.get(exp.id, [])
        ranked = sorted(
            items,
            key=lambda i: (
                -score_evidence(i, skills_by_evidence.get(i.id, set()), terms), i.id
            ),
        )
        kept = ranked[:max_bullets]
        if len(ranked) > len(kept):
            dropped.append(f"{exp.organisation}: {len(ranked) - len(kept)} bullet(s) cut for space")
        return Entry(
            organisation=exp.organisation,
            title=exp.title,
            location=exp.location,
            start_date=exp.start_date,
            end_date=exp.end_date,
            bullets=[
                Bullet(
                    text=i.text,
                    evidence_item_id=i.id,
                    score=score_evidence(i, skills_by_evidence.get(i.id, set()), terms),
                )
                for i in kept
            ],
        )

    def entries_of(kind: str) -> list[Experience]:
        return sorted(
            [e for e in experiences if e.kind == kind],
            key=lambda e: (e.sort_order, e.start_date or ""),
            reverse=True,
        )

    education = [make_entry(e, 3) for e in entries_of("education")]

    work_all = entries_of("work")
    work_entries = [make_entry(e, MAX_BULLETS_PER_WORK) for e in work_all]
    # Rank whole roles by their best evidence, but keep reverse-chronological order on the
    # page: recruiters read a CV as a timeline, not as a ranked list.
    work_entries.sort(key=lambda en: -max((b.score for b in en.bullets), default=0.0))
    kept_work = work_entries[:MAX_WORK]
    for entry in work_entries[MAX_WORK:]:
        dropped.append(f"{entry.organisation}: whole role cut for space")
    kept_work.sort(key=lambda en: (en.start_date or ""), reverse=True)

    project_entries = [make_entry(e, MAX_BULLETS_PER_PROJECT) for e in entries_of("project")]
    project_entries.sort(key=lambda en: -max((b.score for b in en.bullets), default=0.0))
    for entry in project_entries[MAX_PROJECTS:]:
        dropped.append(f"{entry.organisation}: project cut for space")
    kept_projects = project_entries[:MAX_PROJECTS]

    skills = (await session.execute(
        select(Skill).where(Skill.candidate_id == candidate.id)
    )).scalars().all()
    relevant_families = (
        {"backend", "frontend", "infra", "tool"} if track == "tech" else {"finance", "quant"}
    )
    usable = [s for s in skills if s.family in relevant_families and is_clean_skill(s.name)]
    fragments = [
        s.name for s in skills if s.family in relevant_families and not is_clean_skill(s.name)
    ]
    if fragments:
        dropped.append(
            f"{len(fragments)} malformed skill name(s) withheld from the CV "
            f"(e.g. {fragments[0]!r}) — split on a comma inside brackets at import"
        )
    picked = sorted(usable, key=lambda s: (-s.evidence_count, s.name))[:12]
    skills_line = ", ".join(s.name for s in picked)

    langs = (await session.execute(
        select(CandidateLanguage).where(CandidateLanguage.candidate_id == candidate.id)
    )).scalars().all()
    languages_line = ", ".join(f"{lang.language} ({lang.level})" for lang in langs)

    return ContentPlan(
        candidate=candidate,
        opportunity=opp,
        company_name=company.name if company else "",
        track=track,
        education=education,
        work=kept_work,
        projects=kept_projects,
        skills_line=skills_line,
        languages_line=languages_line,
        interests_line=None,
        dropped=dropped,
    )
