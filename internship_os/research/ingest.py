"""Write research produced by Claude into the store, through the same guards as the
gateway path.

the user asked for research to run on Claude credits rather than the free-model gateway. That
changes who does the reasoning, and nothing else: a claim arriving this way is subject to
exactly the checks a locally-generated one is.

  * the quote must appear verbatim in the fetched page, or the excerpt is discarded;
  * confidence is computed from tier, independence, recency and directness — never taken
    from whatever the researcher asserted;
  * survivorship and availability caps still apply.

Anything else would make "researched by Claude" a way to launder an unchecked claim.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.base import today, utcnow
from ..models.research import (
    ClaimEvidence, ResearchClaim, ResearchExcerpt, ResearchRun, ResearchSource,
)
from .confidence import ClaimSignals, score, stale_after
from .orchestrator import quote_is_verbatim
from .sources.fetch import assign_tier, fetch_page


@dataclass
class IncomingClaim:
    topic: str
    statement: str
    epistemic_class: str           # fact | inference | speculation
    quotes: list[dict] = field(default_factory=list)   # [{"url":…, "quote":…}]
    rationale: str = ""


@dataclass
class IngestReport:
    claims: int = 0
    excerpts: int = 0
    discarded: int = 0
    unreachable: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


async def ingest_claims(
    session: AsyncSession,
    *,
    subject_type: str,
    subject_id: int,
    goal: str,
    claims: list[IncomingClaim],
    company_domains: set[str] | None = None,
) -> IngestReport:
    report = IngestReport()
    run = ResearchRun(subject_type=subject_type, subject_id=subject_id, goal=goal,
                      status="running", model="claude")
    session.add(run)
    await session.flush()

    page_cache: dict[str, object] = {}

    for incoming in claims:
        excerpt_rows: list[ResearchExcerpt] = []
        meta: list[tuple[int, str]] = []

        for item in incoming.quotes:
            url, quote = item.get("url", ""), (item.get("quote") or "").strip()
            if not url or not quote:
                continue
            if url not in page_cache:
                try:
                    page_cache[url] = await fetch_page(url, company_domains=company_domains)
                except (httpx.HTTPError, OSError):
                    page_cache[url] = None
            page = page_cache[url]
            if page is None:
                report.unreachable.append(url)
                continue
            # THE guard. A quote that is not literally on the page never becomes evidence,
            # no matter how confident the researcher was.
            if not quote_is_verbatim(quote, page.text):
                report.discarded += 1
                continue

            existing = (await session.execute(
                select(ResearchSource).where(ResearchSource.url_hash == page.sha256[:32])
            )).scalars().first()
            if existing is None:
                existing = ResearchSource(
                    url=page.url, url_hash=page.sha256[:32], domain=page.domain,
                    title=page.title, source_tier=page.tier, accessed_at=utcnow(),
                    content_sha256=page.sha256, raw_path=str(page.path),
                    retrieval_method="claude", is_primary=page.is_primary,
                )
                session.add(existing)
                await session.flush()
            row = ResearchExcerpt(source_id=existing.id, quote=quote)
            session.add(row)
            await session.flush()
            excerpt_rows.append(row)
            meta.append((page.tier, page.domain))

        if not excerpt_rows:
            report.notes.append(f"no verifiable source for: {incoming.statement[:70]}")
            continue

        scored = score(ClaimSignals(
            topic=incoming.topic,
            epistemic_class=incoming.epistemic_class
            if incoming.epistemic_class in {"fact", "inference", "speculation"} else "inference",
            method="synthesis",
            statement=incoming.statement,
            source_tiers=[t for t, _ in meta],
            domains=[d for _, d in meta],
            as_of=today(),
            disconfirming_search_ran=False,
        ))
        claim = ResearchClaim(
            run_id=run.id, subject_type=subject_type, subject_id=subject_id,
            topic=incoming.topic, statement=incoming.statement,
            epistemic_class=scored.epistemic_class, confidence=scored.confidence,
            confidence_rationale=f"{incoming.rationale} | {scored.rationale}",
            method="synthesis", as_of=today(), stale_after=stale_after(incoming.topic, today()),
            n_supporting=scored.n_supporting, n_independent_domains=scored.n_independent_domains,
            bias_flags=scored.bias_flags,
        )
        session.add(claim)
        await session.flush()
        for row in excerpt_rows:
            session.add(ClaimEvidence(claim_id=claim.id, excerpt_id=row.id, stance="supports"))
        report.claims += 1
        report.excerpts += len(excerpt_rows)

    run.status = "done"
    run.finished_at = utcnow()
    run.stop_reason = "ingested"
    await session.commit()
    return report
