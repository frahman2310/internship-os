"""Persist drafts: upsert companies, dedup opportunities, detect meaningful changes.

A changed closing date on something you are already drafting is the alert that actually
matters, so changes are reported rather than silently applied over.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.application import AtsPlatform
from ..models.base import today, utcnow
from ..models.company import Company
from ..models.opportunity import Opportunity, OpportunityStateEvent
from .base import OpportunityDraft, canonical_company

# Seeded from the real listing set: Greenhouse 29%, Workday 19%, tal.net 5%, bespoke 43%.
ATS_SEED = [
    {
        "key": "greenhouse",
        "display_name": "Greenhouse",
        "url_patterns": ["greenhouse.io", "grnh.se", "job-boards.greenhouse.io"],
        "automation_support": "assisted",
        "notes": "Stable field names (job_application[first_name]). Highest-frequency portal.",
    },
    {
        "key": "workday",
        "display_name": "Workday",
        "url_patterns": ["myworkdayjobs.com", "wd1.", "wd3.", "wd5."],
        "automation_support": "manual_only",
        "notes": "Requires account creation with a password — which this system refuses to do.",
    },
    {
        "key": "talnet",
        "display_name": "tal.net",
        "url_patterns": ["tal.net"],
        "automation_support": "assisted",
        "notes": "Used by BlackRock and Bank of America in this set.",
    },
    {
        "key": "lever",
        "display_name": "Lever",
        "url_patterns": ["lever.co", "jobs.lever.co"],
        "automation_support": "assisted",
    },
    {
        "key": "workable",
        "display_name": "Workable",
        "url_patterns": ["workable.com"],
        "automation_support": "assisted",
    },
    {
        "key": "smartrecruiters",
        "display_name": "SmartRecruiters",
        "url_patterns": ["smartrecruiters.com"],
        "automation_support": "manual_only",
    },
    {
        "key": "ashby",
        "display_name": "Ashby",
        "url_patterns": ["ashbyhq.com"],
        "automation_support": "manual_only",
    },
    {
        "key": "bespoke",
        "display_name": "Bespoke career site",
        "url_patterns": [],
        "automation_support": "manual_only",
        "notes": "43% of listings. Prepared answers file + PDFs; you fill the form.",
    },
]


@dataclass
class SyncReport:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    expired: int = 0
    changes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.created + self.updated + self.unchanged


async def looks_throttled(
    session: AsyncSession, results: list, source_adapter: str
) -> bool:
    """Is an empty result set a real 'nothing found', or the source refusing to answer?

    The Trackr API throttles by returning HTTP 200 with an empty array rather than a 429,
    which is indistinguishable from a genuinely empty season. If we have previously stored
    opportunities from this source, an empty response is far more likely to be throttling —
    so treat it as a failed fetch rather than as the truth, and never let it drive state.
    """
    if results:
        return False
    known = await session.scalar(
        select(func.count())
        .select_from(Opportunity)
        .where(Opportunity.source_adapter == source_adapter)
    )
    return bool(known)


async def seed_ats_platforms(session: AsyncSession) -> None:
    existing = set(
        (await session.execute(select(AtsPlatform.key))).scalars().all()
    )
    for row in ATS_SEED:
        if row["key"] not in existing:
            session.add(AtsPlatform(**row))
    await session.flush()


async def classify_ats(session: AsyncSession, url: str | None) -> int | None:
    """Match a URL to a platform by substring; unmatched real URLs fall back to bespoke."""
    if not url:
        return None
    platforms = (await session.execute(select(AtsPlatform))).scalars().all()
    bespoke_id = None
    for p in platforms:
        if p.key == "bespoke":
            bespoke_id = p.id
            continue
        for pattern in p.url_patterns or []:
            if pattern in url:
                return p.id
    return bespoke_id


async def _upsert_company(session: AsyncSession, draft: OpportunityDraft) -> Company:
    canon = canonical_company(draft.company_name)
    company = (
        await session.execute(select(Company).where(Company.canonical_name == canon))
    ).scalar_one_or_none()
    if company is None:
        company = Company(
            name=draft.company_name,
            canonical_name=canon,
            trackr_company_id=draft.company_ref,
            careers_url=draft.careers_url,
            description=draft.company_description,
            sponsors_visa=draft.sponsors_visa,
            uk_housing_link=draft.uk_housing_link,
        )
        session.add(company)
        await session.flush()
    else:
        # Only ever upgrade unknown -> known; never overwrite a known answer with a guess.
        if company.sponsors_visa == "unknown" and draft.sponsors_visa != "unknown":
            company.sponsors_visa = draft.sponsors_visa
            company.updated_at = utcnow()
    return company


# Fields whose change is worth telling the user about.
_WATCHED = ("closing_date", "opening_date", "rolling", "process_raw", "apply_url")


async def upsert_draft(
    session: AsyncSession, draft: OpportunityDraft, report: SyncReport
) -> Opportunity:
    company = await _upsert_company(session, draft)
    key = draft.dedup_key

    opp = (
        await session.execute(select(Opportunity).where(Opportunity.dedup_key == key))
    ).scalar_one_or_none()

    if opp is None:
        opp = Opportunity(
            company_id=company.id,
            source_adapter=draft.source_adapter,
            source_ref=draft.source_ref,
            dedup_key=key,
            title=draft.title,
            programme_type=draft.programme_type,
            season=draft.season,
            region=draft.region,
            industry=draft.industry,
            locations=draft.locations,
            categories=draft.categories,
            track=draft.track,
            is_quant=draft.is_quant,
            url=draft.url,
            apply_url=draft.apply_url,
            ats_platform_id=await classify_ats(session, draft.apply_url),
            opening_date=draft.opening_date,
            closing_date=draft.closing_date,
            rolling=draft.rolling,
            process_raw=draft.process_raw,
            requires_cv=draft.requires_cv,
            requires_cover_letter=draft.requires_cover_letter,
            requires_written_answers=draft.requires_written_answers,
            acceptance_rate=draft.acceptance_rate,
            conversion_rate=draft.conversion_rate,
            eligibility_raw=draft.eligibility_raw,
            notes_raw=draft.notes_raw,
            state="DISCOVERED",
            raw_payload=draft.raw_payload,
        )
        session.add(opp)
        await session.flush()
        session.add(
            OpportunityStateEvent(
                opportunity_id=opp.id, to_state="DISCOVERED", reason="discovered via sync"
            )
        )
        report.created += 1
        return opp

    # Seen before: note what moved, then update.
    changed = []
    for fieldname in _WATCHED:
        old, new = getattr(opp, fieldname), getattr(draft, fieldname)
        if new is not None and old != new:
            changed.append(f"{opp.title} @ {company.name}: {fieldname} {old!r} -> {new!r}")
            setattr(opp, fieldname, new)

    # A track seen in a second industry sync means the programme spans both.
    if draft.track and opp.track and draft.track != opp.track:
        opp.track = "both"

    opp.last_seen_at = utcnow()
    if changed:
        report.updated += 1
        report.changes.extend(changed)
    else:
        report.unchanged += 1
    return opp


async def expire_past_deadline(session: AsyncSession, report: SyncReport) -> None:
    """Anything closed and not yet submitted is EXPIRED. Runs after every sync."""
    from ..strategy.state import TRANSITIONS, transition

    cutoff = today()
    rows = (
        (
            await session.execute(
                select(Opportunity).where(
                    Opportunity.closing_date.is_not(None),
                    Opportunity.closing_date < cutoff,
                )
            )
        )
        .scalars()
        .all()
    )
    for opp in rows:
        if "EXPIRED" in TRANSITIONS.get(opp.state, set()):
            await transition(
                session, opp, "EXPIRED", reason=f"closing date {opp.closing_date} passed"
            )
            report.expired += 1
