"""Infrastructure tables: the job queue and the LLM call log (which is also the cache)."""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, Float, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import enum_col, utcnow


class Job(Base):
    """SQLite-backed queue. Claimed with a single atomic UPDATE ... RETURNING under
    BEGIN IMMEDIATE — the SQLite equivalent of FOR UPDATE SKIP LOCKED.

    ponytail: one in-process worker, polling. Move to multi-worker + Postgres
    SKIP LOCKED only if a backlog ever actually forms.
    """

    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_claim", "state", "next_run_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    state = enum_col("state", ("queued", "running", "done", "failed"), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_run_at: Mapped[str] = mapped_column(String, default=utcnow)
    locked_at: Mapped[str | None] = mapped_column(String, nullable=True)
    locked_by: Mapped[str | None] = mapped_column(String, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=utcnow)


class LlmCall(Base):
    """Log and cache in one table — a cache hit is a lookup on (prompt_sha256, model)."""

    __tablename__ = "llm_calls"
    __table_args__ = (UniqueConstraint("prompt_sha256", "model", name="uq_llm_cache"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    purpose: Mapped[str | None] = mapped_column(String, nullable=True)
    model: Mapped[str] = mapped_column(String)
    prompt_sha256: Mapped[str] = mapped_column(String)
    response: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(String, default=utcnow)
