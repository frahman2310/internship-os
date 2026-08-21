"""Opportunities, their state history, fit assessments and application strategy."""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    Boolean,
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
from .base import GATE_VERDICTS, OPPORTUNITY_STATES, enum_col, utcnow


class Opportunity(Base):
    __tablename__ = "opportunities"
    __table_args__ = (
        UniqueConstraint("dedup_key", name="uq_opp_dedup"),
        Index("ix_opp_state", "state"),
        Index("ix_opp_closing", "closing_date"),
        Index("ix_opp_company", "company_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    source_adapter: Mapped[str] = mapped_column(String)
    source_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    dedup_key: Mapped[str] = mapped_column(String)

    title: Mapped[str] = mapped_column(String)
    programme_type: Mapped[str | None] = mapped_column(String, nullable=True)
    season: Mapped[str | None] = mapped_column(String, nullable=True)
    region: Mapped[str | None] = mapped_column(String, nullable=True)
    industry: Mapped[str | None] = mapped_column(String, nullable=True)
    locations: Mapped[list | None] = mapped_column(JSON, nullable=True)
    categories: Mapped[list | None] = mapped_column(JSON, nullable=True)
    track: Mapped[str | None] = mapped_column(String, nullable=True)  # finance|tech|both
    is_quant: Mapped[bool] = mapped_column(Boolean, default=False)

    url: Mapped[str | None] = mapped_column(String, nullable=True)
    apply_url: Mapped[str | None] = mapped_column(String, nullable=True)
    ats_platform_id: Mapped[int | None] = mapped_column(
        ForeignKey("ats_platforms.id", ondelete="SET NULL"), nullable=True
    )

    opening_date: Mapped[str | None] = mapped_column(String, nullable=True)
    closing_date: Mapped[str | None] = mapped_column(String, nullable=True)
    rolling: Mapped[bool] = mapped_column(Boolean, default=False)

    process_raw: Mapped[str | None] = mapped_column(String, nullable=True)
    requires_cv: Mapped[bool] = mapped_column(Boolean, default=True)
    requires_cover_letter = enum_col(
        "requires_cover_letter", ("yes", "no", "optional", "unknown"), default="unknown"
    )
    requires_written_answers = enum_col(
        "requires_written_answers", ("yes", "no", "optional", "unknown"), default="unknown"
    )
    acceptance_rate: Mapped[str | None] = mapped_column(String, nullable=True)
    conversion_rate: Mapped[str | None] = mapped_column(String, nullable=True)
    eligibility_raw: Mapped[str | None] = mapped_column(String, nullable=True)
    notes_raw: Mapped[str | None] = mapped_column(Text, nullable=True)

    state = enum_col("state", OPPORTUNITY_STATES, default="DISCOVERED")
    state_changed_at: Mapped[str] = mapped_column(String, default=utcnow)
    priority_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    first_seen_at: Mapped[str] = mapped_column(String, default=utcnow)
    last_seen_at: Mapped[str] = mapped_column(String, default=utcnow)
    raw_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class OpportunityStateEvent(Base):
    """Append-only audit of every transition. Written in the same transaction as the move."""

    __tablename__ = "opportunity_state_events"
    __table_args__ = (Index("ix_state_event_opp", "opportunity_id", "occurred_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(
        ForeignKey("opportunities.id", ondelete="CASCADE")
    )
    from_state: Mapped[str | None] = mapped_column(String, nullable=True)
    to_state: Mapped[str] = mapped_column(String)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor = enum_col("actor", ("system", "user", "agent"), default="system")
    occurred_at: Mapped[str] = mapped_column(String, default=utcnow)
    event_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class FitAssessment(Base):
    """Never updated, only appended — latest row wins. Weights live in config, versioned."""

    __tablename__ = "fit_assessments"
    __table_args__ = (Index("ix_fit_opp", "opportunity_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(
        ForeignKey("opportunities.id", ondelete="CASCADE")
    )
    framework_version: Mapped[str] = mapped_column(String)
    eligibility_gate = enum_col("eligibility_gate", GATE_VERDICTS)
    eligibility_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    language_gate = enum_col("language_gate", GATE_VERDICTS)
    language_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    technical: Mapped[int | None] = mapped_column(Integer, nullable=True)
    experience: Mapped[int | None] = mapped_column(Integer, nullable=True)
    behavioral: Mapped[int | None] = mapped_column(Integer, nullable=True)
    career: Mapped[int | None] = mapped_column(Integer, nullable=True)
    location_verdict = enum_col("location_verdict", GATE_VERDICTS, nullable=True)
    overall: Mapped[float | None] = mapped_column(Float, nullable=True)
    verdict_band: Mapped[str | None] = mapped_column(String, nullable=True)
    strengths: Mapped[list | None] = mapped_column(JSON, nullable=True)
    gaps: Mapped[list | None] = mapped_column(JSON, nullable=True)
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    prompt_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    research_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("research_runs.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[str] = mapped_column(String, default=utcnow)


class Strategy(Base):
    """The plan for one application — deliberately separate from the score."""

    __tablename__ = "strategies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(
        ForeignKey("opportunities.id", ondelete="CASCADE")
    )
    positioning_line: Mapped[str | None] = mapped_column(Text, nullable=True)
    narrative_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_item_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    keywords: Mapped[list | None] = mapped_column(JSON, nullable=True)
    gaps_to_acknowledge: Mapped[list | None] = mapped_column(JSON, nullable=True)
    angles: Mapped[list | None] = mapped_column(JSON, nullable=True)  # each carries claim_id
    base_cv = enum_col("base_cv", ("finance", "tech", "master"), nullable=True)
    risks: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=utcnow)
