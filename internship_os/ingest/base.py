"""Source adapter protocol + the normalised draft every source produces.

Two methods, because two more adapters are already specified: `manual` (paste a JD — the
highest-value one, since 43% of the real listing set is bespoke career sites that Trackr
links to but does not describe) and a LinkedIn *manual import* path. Without those already
committed this would be a one-implementation interface and should not exist.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

# Tokens that carry no identity: they vary between how a source writes the same programme.
_NOISE = {
    "programme",
    "program",
    "the",
    "a",
    "for",
    "and",
    "&",
}
_YEAR = re.compile(r"\b(19|20)\d{2}\b")
_PUNCT = re.compile(r"[^\w\s]")
_SPACE = re.compile(r"\s+")


def canonical_company(name: str) -> str:
    """Fold legal suffixes and punctuation so 'Grant Thornton (UK) LLP' == 'grant thornton uk'."""
    s = name.lower().strip()
    s = _PUNCT.sub(" ", s)
    s = _SPACE.sub(" ", s).strip()
    for suffix in (" llp", " ltd", " limited", " inc", " plc", " co", " group", " & co"):
        if s.endswith(suffix):
            s = s[: -len(suffix)].strip()
    return s


def normalize_title(title: str) -> str:
    """Drop the year and the programme/program spelling difference, then sort the rest.

    Token-sorting is deliberate: sources word the same programme in different orders
    ('2027 Summer Internship Program' vs 'Summer Internship Programme 2027').
    Region qualifiers like EMEA are NOT stripped — Trackr lists finance and tech streams
    separately and you apply to one, so they must stay distinct opportunities.
    """
    s = title.lower()
    s = _YEAR.sub(" ", s)
    s = _PUNCT.sub(" ", s)
    tokens = [t for t in _SPACE.sub(" ", s).split() if t and t not in _NOISE]
    return " ".join(sorted(set(tokens)))


def make_dedup_key(company: str, title: str, season: str | None, region: str | None) -> str:
    raw = "|".join(
        [canonical_company(company), normalize_title(title), season or "", region or ""]
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


@dataclass(slots=True)
class OpportunityDraft:
    """Source-agnostic shape. Every adapter maps into this; nothing downstream knows
    which source a row came from."""

    company_name: str
    title: str
    source_adapter: str
    source_ref: str | None = None
    company_ref: str | None = None
    company_description: str | None = None
    careers_url: str | None = None
    sponsors_visa: str = "unknown"  # yes | no | unknown
    uk_housing_link: str | None = None
    url: str | None = None
    apply_url: str | None = None
    season: str | None = None
    region: str | None = None
    industry: str | None = None
    programme_type: str | None = None
    locations: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    track: str | None = None
    is_quant: bool = False
    opening_date: str | None = None
    closing_date: str | None = None
    rolling: bool = False
    process_raw: str | None = None
    requires_cv: bool = True
    requires_cover_letter: str = "unknown"
    requires_written_answers: str = "unknown"
    acceptance_rate: str | None = None
    conversion_rate: str | None = None
    eligibility_raw: str | None = None
    notes_raw: str | None = None
    raw_payload: dict[str, Any] | None = None

    @property
    def dedup_key(self) -> str:
        return make_dedup_key(self.company_name, self.title, self.season, self.region)


class SourceError(RuntimeError):
    """An adapter could not fetch. Carries the source's own error code when it has one."""

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code


class SourceAdapter(Protocol):
    key: str

    async def fetch(self, **scope: Any) -> list[dict]: ...

    def to_draft(self, raw: dict) -> OpportunityDraft: ...
