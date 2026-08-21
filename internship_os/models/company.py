"""Companies and the synthesised, per-topic intelligence held about them."""

from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import enum_col, utcnow

INTEL_TOPICS = (
    "culture",
    "process",
    "comp",
    "tech_stack",
    "recent_news",
    "interview_style",
    "hiring_bar",
    "sponsorship_policy",
    "conversion",
    "red_flags",
)

# Topic half-lives in months — drive `stale_after`. Deadlines rot fast, culture slowly.
TOPIC_HALF_LIFE_MONTHS = {
    "comp": 6,
    "recent_news": 6,
    "conversion": 12,
    "sponsorship_policy": 12,
    "process": 18,
    "interview_style": 18,
    "culture": 36,
    "hiring_bar": 36,
    "tech_stack": 36,
    "red_flags": 18,
}


class Company(Base):
    __tablename__ = "companies"
    __table_args__ = (
        UniqueConstraint("canonical_name", name="uq_company_canon"),
        UniqueConstraint("trackr_company_id", name="uq_company_trackr"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    canonical_name: Mapped[str] = mapped_column(String)
    trackr_company_id: Mapped[str | None] = mapped_column(String, nullable=True)
    website: Mapped[str | None] = mapped_column(String, nullable=True)
    careers_url: Mapped[str | None] = mapped_column(String, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    sponsors_visa = enum_col("sponsors_visa", ("yes", "no", "unknown"), default="unknown")
    uk_housing_link: Mapped[str | None] = mapped_column(String, nullable=True)
    tier: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=utcnow)
    updated_at: Mapped[str] = mapped_column(String, default=utcnow)


class CompanyIntelligence(Base):
    """A synthesised summary for one topic, at one point in time.

    Superseded rather than updated, so what we believed and when stays recoverable.
    Summary prose carries inline [claim:<id>] markers so any sentence expands to its sources.
    """

    __tablename__ = "company_intelligence"
    __table_args__ = (Index("ix_intel_company_topic", "company_id", "topic", "as_of"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    topic = enum_col("topic", INTEL_TOPICS)
    summary_md: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float)
    as_of: Mapped[str] = mapped_column(String)
    stale_after: Mapped[str] = mapped_column(String)
    research_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("research_runs.id", ondelete="SET NULL"), nullable=True
    )
    superseded_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("company_intelligence.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[str] = mapped_column(String, default=utcnow)
