"""Candidate profile — the factual bedrock.

`evidence_items` is the whitelist of quotable facts. Nothing may appear in a generated
document unless it cites a row here. That turns the "never fabricate" rule from a prompt
instruction into a foreign key.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base
from .base import enum_col, utcnow


class Candidate(Base):
    __tablename__ = "candidate"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    full_name: Mapped[str] = mapped_column(String)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    phone: Mapped[str | None] = mapped_column(String, nullable=True)
    location_term: Mapped[str | None] = mapped_column(String, nullable=True)
    location_home: Mapped[str | None] = mapped_column(String, nullable=True)
    nationality: Mapped[str | None] = mapped_column(String, nullable=True)
    visa_status: Mapped[str | None] = mapped_column(String, nullable=True)
    work_rights_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(String, nullable=True)
    github_url: Mapped[str | None] = mapped_column(String, nullable=True)
    cv_language: Mapped[str] = mapped_column(String, default="English")
    source_file: Mapped[str | None] = mapped_column(String, nullable=True)
    source_sha256: Mapped[str | None] = mapped_column(String, nullable=True)
    updated_at: Mapped[str] = mapped_column(String, default=utcnow)

    languages: Mapped[list["CandidateLanguage"]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan"
    )
    experiences: Mapped[list["Experience"]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan"
    )


class CandidateLanguage(Base):
    """Feeds the Language Gate. An undeclared language is a hard fail, so absence matters."""

    __tablename__ = "candidate_languages"
    __table_args__ = (UniqueConstraint("candidate_id", "language", name="uq_lang"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidate.id", ondelete="CASCADE"))
    language: Mapped[str] = mapped_column(String)
    level: Mapped[str] = mapped_column(String)
    note: Mapped[str | None] = mapped_column(String, nullable=True)

    candidate: Mapped[Candidate] = relationship(back_populates="languages")


class Experience(Base):
    __tablename__ = "experiences"
    __table_args__ = (Index("ix_exp_kind_start", "kind", "start_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidate.id", ondelete="CASCADE"))
    kind = enum_col(
        "kind", ("work", "education", "project", "award", "leadership", "certification")
    )
    organisation: Mapped[str] = mapped_column(String)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    location: Mapped[str | None] = mapped_column(String, nullable=True)
    start_date: Mapped[str | None] = mapped_column(String, nullable=True)
    end_date: Mapped[str | None] = mapped_column(String, nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    raw_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_file: Mapped[str | None] = mapped_column(String, nullable=True)
    source_locator: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=utcnow)

    candidate: Mapped[Candidate] = relationship(back_populates="experiences")
    evidence: Mapped[list["EvidenceItem"]] = relationship(
        back_populates="experience", cascade="all, delete-orphan"
    )


class EvidenceItem(Base):
    """One atomic, quotable, source-backed fact about the candidate."""

    __tablename__ = "evidence_items"
    __table_args__ = (
        UniqueConstraint("candidate_id", "normalized_text", name="uq_evidence_norm"),
        Index("ix_evidence_experience", "experience_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidate.id", ondelete="CASCADE"))
    experience_id: Mapped[int | None] = mapped_column(
        ForeignKey("experiences.id", ondelete="SET NULL"), nullable=True
    )
    text: Mapped[str] = mapped_column(Text)
    normalized_text: Mapped[str] = mapped_column(Text)
    category = enum_col(
        "category", ("achievement", "metric", "skill", "tool", "credential", "context")
    )
    metric_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    metric_unit: Mapped[str | None] = mapped_column(String, nullable=True)
    metric_approx: Mapped[bool] = mapped_column(Boolean, default=False)
    verified_by = enum_col(
        "verified_by", ("profile_md", "master_cv", "claude_md", "user_confirmed")
    )
    source_file: Mapped[str] = mapped_column(String)
    source_locator: Mapped[str | None] = mapped_column(String, nullable=True)
    first_seen_at: Mapped[str] = mapped_column(String, default=utcnow)
    last_confirmed_at: Mapped[str] = mapped_column(String, default=utcnow)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    experience: Mapped[Experience | None] = relationship(back_populates="evidence")


class Skill(Base):
    __tablename__ = "skills"
    __table_args__ = (UniqueConstraint("candidate_id", "canonical_name", name="uq_skill_canon"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidate.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String)
    canonical_name: Mapped[str] = mapped_column(String)
    family = enum_col(
        "family",
        ("finance", "quant", "language", "backend", "frontend", "infra", "tool", "soft"),
    )
    self_level: Mapped[str | None] = mapped_column(String, nullable=True)
    evidence_count: Mapped[int] = mapped_column(Integer, default=0)


class EvidenceSkill(Base):
    __tablename__ = "evidence_skills"

    evidence_item_id: Mapped[int] = mapped_column(
        ForeignKey("evidence_items.id", ondelete="CASCADE"), primary_key=True
    )
    skill_id: Mapped[int] = mapped_column(
        ForeignKey("skills.id", ondelete="CASCADE"), primary_key=True
    )
