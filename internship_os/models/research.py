"""Research provenance.

The chain that makes "why do you say that?" a database join rather than a re-prompt:

    claim -> claim_evidence -> excerpt (verbatim span) -> source (url + accessed_at + tier)

Source tiers:
  1 company primary / filings / the candidate's OWN recorded outcomes
  2 reputable press, regulator, university careers service
  3 attributed specialist community (a detailed thread describing a named process)
  4 anonymous forum, aggregator, review-site blurb
  5 LLM parametric memory — never citable alone, may never support a `fact`
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import EPISTEMIC_CLASSES, enum_col, utcnow


class ResearchRun(Base):
    __tablename__ = "research_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subject_type = enum_col("subject_type", ("company", "opportunity", "sector", "self"))
    subject_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    goal: Mapped[str] = mapped_column(Text)
    question_set: Mapped[list | None] = mapped_column(JSON, nullable=True)
    status = enum_col("status", ("running", "done", "failed"), default="running")
    iterations: Mapped[int] = mapped_column(Integer, default=0)
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    budget_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_reason = enum_col(
        "stop_reason",
        ("gaps_closed", "budget", "max_iterations", "error"),
        nullable=True,
    )
    started_at: Mapped[str] = mapped_column(String, default=utcnow)
    finished_at: Mapped[str | None] = mapped_column(String, nullable=True)


class ResearchQuestion(Base):
    """Makes the loop inspectable and resumable, and lets a report state what it doesn't know."""

    __tablename__ = "research_questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("research_runs.id", ondelete="CASCADE"))
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("research_questions.id", ondelete="SET NULL"), nullable=True
    )
    text: Mapped[str] = mapped_column(Text)
    topic: Mapped[str | None] = mapped_column(String, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=5)
    status = enum_col("status", ("open", "answered", "unanswerable"), default="open")
    answered_by_claim_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    iteration: Mapped[int] = mapped_column(Integer, default=0)
    generated_at: Mapped[str] = mapped_column(String, default=utcnow)


class ResearchSource(Base):
    __tablename__ = "research_sources"
    __table_args__ = (
        UniqueConstraint("url_hash", name="uq_source_url"),
        Index("ix_source_domain", "domain"),
        Index("ix_source_tier", "source_tier"),
        CheckConstraint("source_tier BETWEEN 1 AND 5", name="ck_source_tier_range"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    url: Mapped[str] = mapped_column(String)
    url_hash: Mapped[str] = mapped_column(String)
    domain: Mapped[str | None] = mapped_column(String, nullable=True)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    publisher: Mapped[str | None] = mapped_column(String, nullable=True)
    source_tier: Mapped[int] = mapped_column(Integer)
    published_at: Mapped[str | None] = mapped_column(String, nullable=True)
    accessed_at: Mapped[str] = mapped_column(String, default=utcnow)
    content_sha256: Mapped[str | None] = mapped_column(String, nullable=True)
    raw_path: Mapped[str | None] = mapped_column(String, nullable=True)
    retrieval_method = enum_col(
        "retrieval_method",
        ("web_search", "fetch", "api", "manual_import", "user_report"),
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    licence_note: Mapped[str | None] = mapped_column(String, nullable=True)


class ResearchExcerpt(Base):
    """A literal span from a stored body. Verbatim-or-discard is enforced before insert."""

    __tablename__ = "research_excerpts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("research_sources.id", ondelete="CASCADE"))
    quote: Mapped[str] = mapped_column(Text)
    char_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    char_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extracted_at: Mapped[str] = mapped_column(String, default=utcnow)


class ResearchClaim(Base):
    __tablename__ = "research_claims"
    __table_args__ = (
        Index("ix_claim_subject", "subject_type", "subject_id", "topic"),
        Index("ix_claim_stale", "stale_after"),
        Index("ix_claim_class", "epistemic_class"),
        CheckConstraint("confidence BETWEEN 0.05 AND 0.95", name="ck_claim_conf_range"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("research_runs.id", ondelete="SET NULL"), nullable=True
    )
    subject_type: Mapped[str] = mapped_column(String)
    subject_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    topic: Mapped[str] = mapped_column(String)
    statement: Mapped[str] = mapped_column(Text)
    epistemic_class = enum_col("epistemic_class", EPISTEMIC_CLASSES)
    confidence: Mapped[float] = mapped_column(Float)
    confidence_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    method = enum_col(
        "method", ("direct_quote", "synthesis", "llm_inference", "user_report")
    )
    as_of: Mapped[str] = mapped_column(String)
    stale_after: Mapped[str | None] = mapped_column(String, nullable=True)
    n_supporting: Mapped[int] = mapped_column(Integer, default=0)
    n_independent_domains: Mapped[int] = mapped_column(Integer, default=0)
    bias_flags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    superseded_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("research_claims.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[str] = mapped_column(String, default=utcnow)


class ClaimEvidence(Base):
    """Many-to-many with stance, so contradicting evidence is attached, not discarded."""

    __tablename__ = "claim_evidence"

    claim_id: Mapped[int] = mapped_column(
        ForeignKey("research_claims.id", ondelete="CASCADE"), primary_key=True
    )
    excerpt_id: Mapped[int] = mapped_column(
        ForeignKey("research_excerpts.id", ondelete="CASCADE"), primary_key=True
    )
    stance = enum_col("stance", ("supports", "contradicts", "qualifies"), default="supports")
    weight: Mapped[float] = mapped_column(Float, default=1.0)


class Contradiction(Base):
    __tablename__ = "contradictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    claim_a_id: Mapped[int] = mapped_column(ForeignKey("research_claims.id", ondelete="CASCADE"))
    claim_b_id: Mapped[int] = mapped_column(ForeignKey("research_claims.id", ondelete="CASCADE"))
    kind = enum_col("kind", ("direct", "partial", "temporal"))
    detected_at: Mapped[str] = mapped_column(String, default=utcnow)
    resolution = enum_col(
        "resolution",
        ("unresolved", "a_wins", "b_wins", "both_qualified", "user_resolved"),
        default="unresolved",
    )
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
