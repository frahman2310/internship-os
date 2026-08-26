"""Every written piece, stored against the firm it was written for.

Pieces live in `documents` / `document_versions` rather than in a new table, because those
already carry the property this needs: **each save is an immutable numbered version**. That
turns the edit history into the training signal for free — the diff between version N and
N+1 is precisely one act of the user correcting the machine, with no separate capture step to
forget or get out of sync.

A piece is addressed by (opportunity, kind, slug), so "the Rothschild cover letter" or
"the BoA answer on teamwork" is recallable by name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.company import Company
from ..models.document import Document, DocumentVersion
from ..models.opportunity import Opportunity

KINDS = ("cover_letter", "form_answers", "cv", "followup", "interview_prep")


@dataclass
class Piece:
    document_id: int
    version_id: int
    version: int
    opportunity_id: int | None
    company: str
    role: str
    kind: str
    slug: str
    title: str
    text: str
    status: str
    created_at: str
    word_count: int = 0
    edited_by_user: bool = False

    def to_json(self) -> dict:
        return {
            "document_id": self.document_id, "version_id": self.version_id,
            "version": self.version, "opportunity_id": self.opportunity_id,
            "company": self.company, "role": self.role, "kind": self.kind,
            "slug": self.slug, "title": self.title, "text": self.text,
            "status": self.status, "created_at": self.created_at,
            "word_count": self.word_count, "edited_by_user": self.edited_by_user,
        }


def words(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", text or ""))


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-") or "piece"


async def save_piece(
    session: AsyncSession,
    *,
    opportunity_id: int | None,
    kind: str,
    slug: str,
    title: str,
    text: str,
    by_user: bool = False,
) -> Piece:
    """Write a new version. Never overwrites: the previous version stays readable, which
    is what makes the learning loop possible."""
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}")

    doc = (await session.execute(
        select(Document).where(
            Document.opportunity_id == opportunity_id,
            Document.kind == kind,
            Document.slug == slug,
        )
    )).scalars().first()
    if doc is None:
        doc = Document(opportunity_id=opportunity_id, kind=kind, slug=slug)
        session.add(doc)
        await session.flush()

    last = (await session.execute(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == doc.id)
        .order_by(DocumentVersion.version.desc())
    )).scalars().first()

    # An identical save is not an edit; recording it would pollute the learning signal.
    if last is not None and (last.source_text or "").strip() == text.strip():
        return await _as_piece(session, doc, last, title)

    version = DocumentVersion(
        document_id=doc.id,
        version=(last.version if last else 0) + 1,
        status="draft",
        source_format="markdown",
        source_text=text,
        created_by="user" if by_user else "agent",
        base_cv=title,  # reused as the human-facing label; no schema change needed
    )
    session.add(version)
    await session.flush()
    doc.current_version_id = version.id
    await session.commit()
    return await _as_piece(session, doc, version, title)


async def _as_piece(session: AsyncSession, doc: Document, version: DocumentVersion,
                    title: str | None = None) -> Piece:
    company, role = "", ""
    if doc.opportunity_id:
        opp = await session.get(Opportunity, doc.opportunity_id)
        if opp:
            role = opp.title or ""
            comp = await session.get(Company, opp.company_id)
            company = comp.name if comp else ""
    return Piece(
        document_id=doc.id, version_id=version.id, version=version.version,
        opportunity_id=doc.opportunity_id, company=company, role=role,
        kind=doc.kind, slug=doc.slug, title=title or version.base_cv or doc.slug,
        text=version.source_text or "", status=version.status,
        created_at=version.created_at, word_count=words(version.source_text or ""),
        edited_by_user=version.created_by == "user",
    )


@dataclass
class FirmPieces:
    company: str
    role: str
    opportunity_id: int | None
    deadline: str | None = None
    pieces: list[Piece] = field(default_factory=list)


async def library(session: AsyncSession) -> list[FirmPieces]:
    """Everything written, grouped by firm — the recall surface."""
    docs = (await session.execute(select(Document))).scalars().all()
    grouped: dict[int | None, FirmPieces] = {}
    for doc in docs:
        current = None
        if doc.current_version_id:
            current = await session.get(DocumentVersion, doc.current_version_id)
        if current is None:
            current = (await session.execute(
                select(DocumentVersion).where(DocumentVersion.document_id == doc.id)
                .order_by(DocumentVersion.version.desc())
            )).scalars().first()
        if current is None:
            continue
        piece = await _as_piece(session, doc, current)
        key = doc.opportunity_id
        if key not in grouped:
            deadline = None
            if doc.opportunity_id:
                opp = await session.get(Opportunity, doc.opportunity_id)
                deadline = (opp.raw_payload or {}).get("deadline") or (opp.closing_date if opp else None)
            grouped[key] = FirmPieces(company=piece.company or "Unassigned",
                                      role=piece.role, opportunity_id=key, deadline=deadline)
        grouped[key].pieces.append(piece)

    out = sorted(grouped.values(), key=lambda g: (g.deadline or "zzzz", g.company))
    for g in out:
        g.pieces.sort(key=lambda p: (p.kind, p.slug))
    return out


async def versions_of(session: AsyncSession, document_id: int) -> list[DocumentVersion]:
    return list((await session.execute(
        select(DocumentVersion).where(DocumentVersion.document_id == document_id)
        .order_by(DocumentVersion.version)
    )).scalars().all())
