"""The two rules the system's trustworthiness rests on.

If these tests pass, no code path — present or future — can quietly fabricate a fact into
a document or approve an ungrounded claim.
"""

from __future__ import annotations

import pytest

from internship_os.models import InvariantViolation
from internship_os.models.document import Document, DocumentClaim, DocumentVersion
from internship_os.models.research import (
    ClaimEvidence,
    ResearchClaim,
    ResearchExcerpt,
    ResearchSource,
)


async def _make_source_and_excerpt(session) -> ResearchExcerpt:
    src = ResearchSource(
        url="https://example.com/careers",
        url_hash="hash-1",
        domain="example.com",
        source_tier=1,
        retrieval_method="fetch",
        is_primary=True,
    )
    session.add(src)
    await session.flush()
    exc = ResearchExcerpt(source_id=src.id, quote="We sponsor Skilled Worker visas.")
    session.add(exc)
    await session.flush()
    return exc


class TestFactClaimsNeedEvidence:
    async def test_fact_without_evidence_is_rejected(self, session):
        session.add(
            ResearchClaim(
                subject_type="company",
                subject_id=1,
                topic="sponsorship_policy",
                statement="They sponsor visas.",
                epistemic_class="fact",
                confidence=0.9,
                method="direct_quote",
                as_of="2026-08-06",
            )
        )
        with pytest.raises(InvariantViolation, match="no supporting excerpt"):
            await session.commit()

    async def test_fact_with_supporting_excerpt_is_allowed(self, session):
        excerpt = await _make_source_and_excerpt(session)
        claim = ResearchClaim(
            subject_type="company",
            subject_id=1,
            topic="sponsorship_policy",
            statement="They sponsor visas.",
            epistemic_class="fact",
            confidence=0.9,
            method="direct_quote",
            as_of="2026-08-06",
        )
        session.add(claim)
        await session.flush()
        session.add(
            ClaimEvidence(claim_id=claim.id, excerpt_id=excerpt.id, stance="supports")
        )
        await session.commit()
        assert claim.id is not None

    async def test_speculation_without_evidence_is_allowed(self, session):
        """Speculation may stand alone — it just cannot be dressed up as fact."""
        session.add(
            ResearchClaim(
                subject_type="company",
                subject_id=1,
                topic="hiring_bar",
                statement="They probably prefer target-school candidates.",
                epistemic_class="speculation",
                confidence=0.3,
                method="llm_inference",
                as_of="2026-08-06",
            )
        )
        await session.commit()

    async def test_confidence_is_never_certain(self, session):
        """The CHECK constraint refuses 1.0 — nothing here is ever certain."""
        excerpt = await _make_source_and_excerpt(session)
        claim = ResearchClaim(
            subject_type="company",
            subject_id=1,
            topic="process",
            statement="Four rounds.",
            epistemic_class="fact",
            confidence=1.0,
            method="direct_quote",
            as_of="2026-08-06",
        )
        session.add(claim)
        with pytest.raises(Exception):  # noqa: B017 - IntegrityError from CHECK
            await session.flush()
            session.add(
                ClaimEvidence(claim_id=claim.id, excerpt_id=excerpt.id)
            )
            await session.commit()


class TestApprovalNeedsGrounding:
    async def _doc_version(self, session, status="draft") -> DocumentVersion:
        doc = Document(kind="cv", slug="test-cv")
        session.add(doc)
        await session.flush()
        ver = DocumentVersion(document_id=doc.id, version=1, status=status)
        session.add(ver)
        await session.flush()
        return ver

    async def test_cannot_approve_with_ungrounded_claim(self, session):
        ver = await self._doc_version(session)
        session.add(
            DocumentClaim(
                document_version_id=ver.id,
                text="Led a team of 40 engineers at Goldman Sachs.",
                grounding="ungrounded",
            )
        )
        await session.commit()

        ver.status = "approved"
        with pytest.raises(InvariantViolation, match="ungrounded"):
            await session.commit()

    async def test_can_approve_when_all_grounded(self, session):
        ver = await self._doc_version(session)
        session.add(
            DocumentClaim(
                document_version_id=ver.id,
                text="Built a QR ordering platform piloted with paying venues.",
                grounding="grounded",
            )
        )
        await session.commit()

        ver.status = "approved"
        await session.commit()
        assert ver.status == "approved"

    async def test_reframed_counts_as_grounded(self, session):
        """Rephrasing an evidence item for a posting's keywords is allowed; inventing is not."""
        ver = await self._doc_version(session)
        session.add(
            DocumentClaim(
                document_version_id=ver.id,
                text="Shipped a multi-tenant SaaS to production.",
                grounding="reframed",
            )
        )
        await session.commit()
        ver.status = "approved"
        await session.commit()
        assert ver.status == "approved"
