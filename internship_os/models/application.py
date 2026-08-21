"""Applications, the approval gate, outcomes and experiments."""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import enum_col, utcnow

# Fields the system must never infer or auto-fill. Yours to give or decline.
RESERVED_FOR_HUMAN = frozenset(
    {
        "gender",
        "ethnicity",
        "disability",
        "veteran_status",
        "date_of_birth",
        "national_insurance_number",
        "passport_number",
        "salary_expectation",
    }
)


class AtsPlatform(Base):
    __tablename__ = "ats_platforms"
    __table_args__ = (UniqueConstraint("key", name="uq_ats_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String)
    display_name: Mapped[str] = mapped_column(String)
    url_patterns: Mapped[list | None] = mapped_column(JSON, nullable=True)
    automation_support = enum_col(
        "automation_support", ("assisted", "manual_only", "refused"), default="manual_only"
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (UniqueConstraint("opportunity_id", name="uq_app_opp"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(
        ForeignKey("opportunities.id", ondelete="CASCADE")
    )
    ats_platform_id: Mapped[int | None] = mapped_column(
        ForeignKey("ats_platforms.id", ondelete="SET NULL"), nullable=True
    )
    portal_url: Mapped[str | None] = mapped_column(String, nullable=True)
    method = enum_col("method", ("browser_assisted", "manual"), default="manual")
    cv_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("document_versions.id", ondelete="SET NULL"), nullable=True
    )
    cover_letter_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("document_versions.id", ondelete="SET NULL"), nullable=True
    )
    answers_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("document_versions.id", ondelete="SET NULL"), nullable=True
    )
    submitted_at: Mapped[str | None] = mapped_column(String, nullable=True)
    confirmation_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=utcnow)


class ApplicationAnswer(Base):
    __tablename__ = "application_answers"
    __table_args__ = (Index("ix_answer_app", "application_id", "semantic_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE")
    )
    field_key: Mapped[str | None] = mapped_column(String, nullable=True)
    field_label_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    semantic_key: Mapped[str | None] = mapped_column(String, nullable=True)
    answer_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    char_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    char_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source = enum_col("source", ("profile", "evidence", "generated", "user"), default="generated")
    evidence_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("evidence_items.id", ondelete="SET NULL"), nullable=True
    )
    approved_at: Mapped[str | None] = mapped_column(String, nullable=True)


class ApplicationEvent(Base):
    __tablename__ = "application_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE")
    )
    kind = enum_col(
        "kind",
        (
            "drafted",
            "review_requested",
            "approved",
            "form_opened",
            "field_filled",
            "blocked",
            "escalated",
            "submitted",
            "confirmation_received",
            "stage_update",
        ),
    )
    at: Mapped[str] = mapped_column(String, default=utcnow)
    actor = enum_col("actor", ("system", "user", "agent"), default="system")
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    screenshot_path: Mapped[str | None] = mapped_column(String, nullable=True)


class Approval(Base):
    """The architectural permission boundary.

    `scope_hash` binds the approval to an exact payload — every field value, document id
    and file hash. submit() recomputes it from the live page and aborts on mismatch, so an
    approval cannot be reused for different content. Single-use, and it expires.
    """

    __tablename__ = "approvals"
    __table_args__ = (Index("ix_approval_subject", "subject_type", "subject_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subject_type = enum_col("subject_type", ("document_version", "application_submission"))
    subject_id: Mapped[int] = mapped_column(Integer)
    scope_hash: Mapped[str] = mapped_column(String)
    granted_at: Mapped[str] = mapped_column(String, default=utcnow)
    expires_at: Mapped[str] = mapped_column(String)
    consumed_at: Mapped[str | None] = mapped_column(String, nullable=True)
    revoked_at: Mapped[str | None] = mapped_column(String, nullable=True)
    confirmation_phrase: Mapped[str | None] = mapped_column(String, nullable=True)


class Outcome(Base):
    """Your real results. Also written as a tier-1 research source — the learning loop
    closes through the same provenance system as everything else."""

    __tablename__ = "outcomes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE")
    )
    status = enum_col(
        "status",
        (
            "in_progress",
            "oa_invited",
            "interview_invited",
            "assessment_centre",
            "offer",
            "hired",
            "rejected",
            "no_response",
            "withdrawn",
        ),
    )
    stage_reached: Mapped[str | None] = mapped_column(String, nullable=True)
    occurred_at: Mapped[str] = mapped_column(String, default=utcnow)
    resolved_at: Mapped[str | None] = mapped_column(String, nullable=True)
    feedback_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    feedback_verbatim: Mapped[bool] = mapped_column(Boolean, default=False)
    days_to_response: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lessons: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=utcnow)


class Experiment(Base):
    """Cannot report supported/refuted below min_n. With ~40 applications, honest
    answer is usually 'inconclusive' — the design says so rather than pattern-matching noise."""

    __tablename__ = "experiments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hypothesis: Mapped[str] = mapped_column(Text)
    variable: Mapped[str | None] = mapped_column(String, nullable=True)
    arms: Mapped[list | None] = mapped_column(JSON, nullable=True)
    application_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    min_n: Mapped[int] = mapped_column(Integer, default=8)
    started_at: Mapped[str] = mapped_column(String, default=utcnow)
    result = enum_col(
        "result", ("running", "inconclusive", "supported", "refuted"), default="running"
    )
    analysis_note: Mapped[str | None] = mapped_column(Text, nullable=True)
