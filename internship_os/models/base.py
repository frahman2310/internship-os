"""Shared column helpers and conventions.

Conventions (chosen for Postgres-portability):
  * enums are TEXT + CHECK, not native enums — greppable in sqlite3, portable later
  * timestamps are ISO-8601 UTC strings, not driver-specific datetimes
  * every table gets an INTEGER primary key
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import CheckConstraint, String
from sqlalchemy.orm import mapped_column


def utcnow() -> str:
    """ISO-8601 UTC, second precision, always suffixed Z."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def today() -> str:
    return datetime.now(UTC).date().isoformat()


def enum_col(name: str, values: tuple[str, ...], **kw):
    """A TEXT column constrained to `values` by a named CHECK constraint."""
    quoted = ", ".join(f"'{v}'" for v in values)
    nullable = kw.pop("nullable", False)
    null_clause = f"{name} IS NULL OR " if nullable else ""
    return mapped_column(
        String,
        CheckConstraint(f"({null_clause}{name} IN ({quoted}))", name=f"ck_{name}"),
        nullable=nullable,
        **kw,
    )


# --- vocabularies (single source of truth; imported by services and prompts) ---

OPPORTUNITY_STATES = (
    "DISCOVERED",
    "QUALIFIED",
    "RESEARCHING",
    "READY_TO_APPLY",
    "DRAFTING",
    "AWAITING_REVIEW",
    "APPROVED",
    "APPLYING",
    "SUBMITTED",
    "ASSESSMENT",
    "INTERVIEW",
    "OFFER",
    "REJECTED",
    "REJECTED_BY_GATE",
    "WITHDRAWN",
    "EXPIRED",
)

TERMINAL_STATES = frozenset({"OFFER", "REJECTED", "REJECTED_BY_GATE", "WITHDRAWN", "EXPIRED"})

EPISTEMIC_CLASSES = ("fact", "inference", "speculation")
GROUNDING = ("grounded", "reframed", "ungrounded")
GATE_VERDICTS = ("pass", "fail", "flag")
