"""Generated documents and the grounding audit, stored as data rather than chat output."""

from __future__ import annotations

from sqlalchemy import JSON, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import GROUNDING, enum_col, utcnow


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    opportunity_id: Mapped[int | None] = mapped_column(
        ForeignKey("opportunities.id", ondelete="CASCADE"), nullable=True
    )
    kind = enum_col(
        "kind", ("cv", "cover_letter", "form_answers", "followup", "interview_prep")
    )
    slug: Mapped[str] = mapped_column(String)
    current_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=utcnow)


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (UniqueConstraint("document_id", "version", name="uq_docver"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    status = enum_col(
        "status",
        ("draft", "reviewed", "revised", "approved", "submitted", "superseded"),
        default="draft",
    )
    source_format = enum_col("source_format", ("latex", "text", "markdown"), default="latex")
    source_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    rendered_path: Mapped[str | None] = mapped_column(String, nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    verify_report: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    base_cv: Mapped[str | None] = mapped_column(String, nullable=True)
    strategy_id: Mapped[int | None] = mapped_column(
        ForeignKey("strategies.id", ondelete="SET NULL"), nullable=True
    )
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    created_by = enum_col("created_by", ("agent", "user"), default="agent")
    created_at: Mapped[str] = mapped_column(String, default=utcnow)


class DocumentClaim(Base):
    """One factual span in a generated document, tagged with how it is grounded.

    A version cannot reach `approved` while any child row here is `ungrounded`
    (enforced in invariants.py). This is apply.md's grounding audit as a constraint.
    """

    __tablename__ = "document_claims"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_version_id: Mapped[int] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE")
    )
    span_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    span_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text: Mapped[str] = mapped_column(Text)
    evidence_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("evidence_items.id", ondelete="SET NULL"), nullable=True
    )
    claim_id: Mapped[int | None] = mapped_column(
        ForeignKey("research_claims.id", ondelete="SET NULL"), nullable=True
    )
    grounding = enum_col("grounding", GROUNDING, default="ungrounded")
    audit_note: Mapped[str | None] = mapped_column(Text, nullable=True)
