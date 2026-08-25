"""Application batches: ten roles at a time, each taken to a finished, reviewable package.

The unit of work is an application, not a research sweep. A batch is done when every role
in it has research, a tailored CV, a cover letter if required, drafted answers for every
written question, and an autofill plan — all grounded, all reviewed by the user.

Ordering is by urgency, not by prestige. A rolling deadline is not a relaxed one: seats
fill as people apply, so a role open since April is more urgent than its blank deadline
suggests.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.document import Document, DocumentClaim, DocumentVersion
from ..models.research import ResearchClaim
from .live import LiveRole, batches, live_roles

BATCH_SIZE = 10


@dataclass
class Readiness:
    role: LiveRole
    claims: int = 0
    strong_claims: int = 0
    has_cv: bool = False
    has_cover: bool = False
    ungrounded: int = 0
    needs_cover: bool = False
    needs_answers: bool = False

    @property
    def stage(self) -> str:
        if not self.claims:
            return "unresearched"
        if self.strong_claims < 3:
            return "thin research"
        if not self.has_cv:
            return "needs CV"
        if self.needs_cover and not self.has_cover:
            return "needs cover letter"
        if self.ungrounded:
            return "ungrounded — blocked"
        return "ready to review"

    @property
    def blocked(self) -> bool:
        return self.ungrounded > 0


@dataclass
class Batch:
    index: int
    total: int
    items: list[Readiness] = field(default_factory=list)

    @property
    def ready(self) -> int:
        return sum(1 for i in self.items if i.stage == "ready to review")


def _requirements(role: LiveRole) -> tuple[bool, bool]:
    """(cover letter required, written answers required), read from Trackr's own fields."""
    p = role.opportunity.raw_payload or {}
    cover = str(p.get("coverLetter") or "").strip().lower() in {"yes", "true", "required"}
    answers = str(p.get("writtenAnswers") or "").strip().lower() in {"yes", "true", "required"}
    return cover, answers


async def load_batch(
    session: AsyncSession,
    *,
    industry: str = "Finance",
    index: int = 1,
    size: int = BATCH_SIZE,
) -> Batch:
    roles = await live_roles(session, industry=industry)
    groups = batches(roles, size)
    if not groups:
        return Batch(index=1, total=0)
    index = max(1, min(index, len(groups)))
    items: list[Readiness] = []

    for role in groups[index - 1]:
        opp_id = role.opportunity.id
        claims = (await session.execute(
            select(ResearchClaim.confidence).where(
                ResearchClaim.subject_id == opp_id,
                ResearchClaim.superseded_by_id.is_(None),
            )
        )).scalars().all()
        docs = (await session.execute(
            select(Document.kind, Document.current_version_id).where(
                Document.opportunity_id == opp_id
            )
        )).all()
        kinds = {k for k, _ in docs}
        version_ids = [v for _, v in docs if v]
        ungrounded = 0
        if version_ids:
            ungrounded = await session.scalar(
                select(func.count()).select_from(DocumentClaim).where(
                    DocumentClaim.document_version_id.in_(version_ids),
                    DocumentClaim.grounding == "ungrounded",
                )
            ) or 0
        needs_cover, needs_answers = _requirements(role)
        items.append(Readiness(
            role=role,
            claims=len(claims),
            strong_claims=sum(1 for c in claims if c >= 0.6),
            has_cv="cv" in kinds,
            has_cover="cover_letter" in kinds,
            ungrounded=ungrounded,
            needs_cover=needs_cover,
            needs_answers=needs_answers,
        ))
    return Batch(index=index, total=len(groups), items=items)


async def batch_overview(session: AsyncSession, industry: str = "Finance") -> tuple[int, int]:
    roles = await live_roles(session, industry=industry)
    return len(roles), len(batches(roles, BATCH_SIZE))
