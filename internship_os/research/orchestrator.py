"""The research loop: plan → recall → retrieve → extract → claim → red-team → gap → stop.

Two rules make the output trustworthy rather than merely fluent:

  * **Verbatim or discard.** A quote that does not literally appear in the stored page is
    dropped before an excerpt row exists. Invented quotes are therefore not a risk to be
    prompted against; they are unrepresentable.
  * **Recall before retrieve.** A question already answered by a non-stale claim closes
    without a network call, so researching the tenth bulge-bracket bank is far cheaper
    than the first.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.base import today, utcnow
from ..models.research import (
    ClaimEvidence,
    ResearchClaim,
    ResearchExcerpt,
    ResearchQuestion,
    ResearchRun,
    ResearchSource,
)
from . import agents
from .confidence import ClaimSignals, score, stale_after
from .sources.fetch import fetch_page, web_search

DEPTH = {  # (questions, pages per question, iterations)
    "quick": (3, 2, 1),
    "standard": (6, 3, 1),
    "deep": (8, 4, 2),
    "extreme": (10, 5, 3),
}


def normalise_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def quote_is_verbatim(quote: str, page_text: str) -> bool:
    """Whitespace-insensitive containment. Anything looser would let paraphrase through."""
    if len(quote.strip()) < 10:
        return False
    return normalise_ws(quote) in normalise_ws(page_text)


@dataclass
class RunReport:
    run_id: int | None = None
    questions: int = 0
    recalled: int = 0
    pages_fetched: int = 0
    excerpts: int = 0
    discarded_quotes: int = 0
    claims: int = 0
    open_questions: list[str] = field(default_factory=list)
    stop_reason: str = "gaps_closed"


async def recall(
    session: AsyncSession, subject_type: str, subject_id: int, topic: str
) -> list[ResearchClaim]:
    """Non-superseded, non-stale claims already held for this subject and topic."""
    rows = (
        (
            await session.execute(
                select(ResearchClaim).where(
                    ResearchClaim.subject_type == subject_type,
                    ResearchClaim.subject_id == subject_id,
                    ResearchClaim.topic == topic,
                    ResearchClaim.superseded_by_id.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    now = today()
    return [r for r in rows if not r.stale_after or r.stale_after >= now]


async def _store_source(session: AsyncSession, page) -> ResearchSource:
    existing = (
        await session.execute(
            select(ResearchSource).where(ResearchSource.url_hash == page.sha256[:32])
        )
    ).scalars().first()
    if existing:
        return existing
    source = ResearchSource(
        url=page.url,
        url_hash=page.sha256[:32],
        domain=page.domain,
        title=page.title,
        source_tier=page.tier,
        accessed_at=utcnow(),
        content_sha256=page.sha256,
        raw_path=str(page.path),
        retrieval_method="fetch",
        is_primary=page.is_primary,
    )
    session.add(source)
    await session.flush()
    return source


async def run_research(
    session: AsyncSession,
    *,
    company: str,
    role: str,
    subject_type: str = "company",
    subject_id: int | None = None,
    goal: str | None = None,
    depth: str = "standard",
    company_domains: set[str] | None = None,
) -> RunReport:
    n_questions, pages_per_q, iterations = DEPTH.get(depth, DEPTH["standard"])
    goal = goal or f"Understand how {company} recruits for {role} and what it rewards."
    report = RunReport()

    run = ResearchRun(
        subject_type=subject_type,
        subject_id=subject_id,
        goal=goal,
        status="running",
        model=",".join([]),
    )
    session.add(run)
    await session.flush()
    report.run_id = run.id

    plan = await agents.plan_research(company, role, goal, n=n_questions)
    questions = plan.questions[:n_questions]
    report.questions = len(questions)

    open_questions: list[str] = []

    for iteration in range(iterations):
        for planned in questions:
            q_row = ResearchQuestion(
                run_id=run.id,
                text=planned.text,
                topic=planned.topic,
                priority=planned.priority,
                iteration=iteration,
            )
            session.add(q_row)
            await session.flush()

            # --- recall: skip the network when we already know -------------------------
            if subject_id is not None:
                known = await recall(session, subject_type, subject_id, planned.topic)
                strong = [c for c in known if c.confidence >= 0.6]
                if strong:
                    q_row.status = "answered"
                    q_row.answered_by_claim_id = strong[0].id
                    report.recalled += 1
                    continue

            # --- retrieve ---------------------------------------------------------------
            hits = await web_search(f"{company} {planned.text}", limit=pages_per_q + 2)
            excerpt_rows: list[ResearchExcerpt] = []
            excerpt_meta: list[tuple[int, str]] = []  # (tier, domain)

            for hit in hits[:pages_per_q]:
                page = await fetch_page(hit["url"], company_domains=company_domains)
                if page is None:
                    continue
                report.pages_fetched += 1
                source = await _store_source(session, page)

                extraction = await agents.extract_excerpts(planned.text, page.text)
                for excerpt in extraction.excerpts:
                    # THE guard: no verbatim match, no row.
                    if not quote_is_verbatim(excerpt.quote, page.text):
                        report.discarded_quotes += 1
                        continue
                    row = ResearchExcerpt(source_id=source.id, quote=excerpt.quote.strip())
                    session.add(row)
                    await session.flush()
                    excerpt_rows.append(row)
                    excerpt_meta.append((page.tier, page.domain))

            if not excerpt_rows:
                q_row.status = "unanswerable"
                open_questions.append(planned.text)
                continue

            # --- claim -----------------------------------------------------------------
            synthesis = await agents.synthesise_claims(
                planned.text, [r.quote for r in excerpt_rows]
            )
            for draft in synthesis.claims[:3]:
                indexes = [i for i in draft.excerpt_indexes if 0 <= i < len(excerpt_rows)]
                if not indexes:
                    indexes = list(range(min(2, len(excerpt_rows))))
                tiers = [excerpt_meta[i][0] for i in indexes]
                domains = [excerpt_meta[i][1] for i in indexes]

                red = await agents.red_team(draft.statement)

                scored = score(
                    ClaimSignals(
                        topic=planned.topic,
                        epistemic_class=draft.epistemic_class
                        if draft.epistemic_class in {"fact", "inference", "speculation"}
                        else "inference",
                        method=draft.method
                        if draft.method in {"direct_quote", "synthesis", "llm_inference"}
                        else "synthesis",
                        statement=draft.statement,
                        source_tiers=tiers,
                        domains=domains,
                        as_of=today(),
                        disconfirming_search_ran=bool(red.disconfirming_queries),
                    )
                )

                claim = ResearchClaim(
                    run_id=run.id,
                    subject_type=subject_type,
                    subject_id=subject_id,
                    topic=planned.topic,
                    statement=draft.statement,
                    epistemic_class=scored.epistemic_class,
                    confidence=scored.confidence,
                    confidence_rationale=f"{draft.rationale} | {scored.rationale}",
                    method=draft.method
                    if draft.method in {"direct_quote", "synthesis", "llm_inference"}
                    else "synthesis",
                    as_of=today(),
                    stale_after=stale_after(planned.topic, today()),
                    n_supporting=scored.n_supporting,
                    n_independent_domains=scored.n_independent_domains,
                    bias_flags=scored.bias_flags + red.concerns[:2],
                )
                session.add(claim)
                await session.flush()
                for i in indexes:
                    session.add(
                        ClaimEvidence(
                            claim_id=claim.id,
                            excerpt_id=excerpt_rows[i].id,
                            stance="supports",
                        )
                    )
                report.claims += 1
                report.excerpts += len(indexes)

            q_row.status = "answered"

        if iteration + 1 < iterations and open_questions:
            gaps = await agents.find_gaps(goal, [], open_questions)
            questions = gaps.follow_up_questions[:n_questions]
            open_questions = []
            if not questions:
                report.stop_reason = "gaps_closed"
                break
        else:
            break

    run.status = "done"
    run.iterations = iterations
    run.finished_at = utcnow()
    run.stop_reason = report.stop_reason
    report.open_questions = open_questions
    return report
