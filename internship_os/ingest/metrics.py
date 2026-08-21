"""Pull numbers out of achievement text.

Parsed structurally rather than by asking a model, for one reason: the grounding audit's
job is to catch number escalation ("330+ tests" quietly becoming "500+"). A check that
depends on an LLM re-reading prose can drift. A parsed float cannot.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_CURRENCY = {"$": "USD", "£": "GBP", "€": "EUR"}

# Ordered: first match wins, so more specific patterns come first.
_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(?P<sym>[$£€])\s?(?P<num>[\d,]+(?:\.\d+)?)\s?(?P<mult>[km])?\b", re.I), "money"),
    (re.compile(r"(?P<approx>~|about |roughly )?(?P<num>\d+(?:\.\d+)?)\s?%"), "percent"),
    (re.compile(r"(?P<num>\d+(?:\.\d+)?)x\s?[–\-—]\s?(?P<num2>\d+(?:\.\d+)?)x"), "multiple_range"),
    (re.compile(r"(?P<num>\d+(?:\.\d+)?)x\b"), "multiple"),
    (re.compile(r"(?P<num>[\d,]+)\+"), "count_min"),
    (re.compile(r"\b(?P<num>[\d,]+)\s+(?:tests|attendees|delegates|staff|entrants|members)\b", re.I), "count"),
]


@dataclass(slots=True)
class Metric:
    value: float
    unit: str
    approx: bool = False

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Metric({self.value}, {self.unit!r}, approx={self.approx})"


def _to_float(raw: str) -> float:
    return float(raw.replace(",", ""))


def extract_metric(text: str) -> Metric | None:
    """Return the most salient metric in a line, or None.

    'Most salient' means the first match in pattern order, not the first in the string:
    a money figure carries more weight than a bare count in the same sentence.
    """
    for pattern, kind in _PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        groups = m.groupdict()

        if kind == "money":
            value = _to_float(groups["num"])
            mult = (groups.get("mult") or "").lower()
            value *= {"k": 1_000, "m": 1_000_000}.get(mult, 1)
            approx = bool(re.search(r"(~|about|roughly)\s*[$£€]", text, re.I))
            return Metric(value, _CURRENCY.get(groups["sym"], "USD"), approx)

        if kind == "percent":
            return Metric(_to_float(groups["num"]), "%", approx=bool(groups.get("approx")))

        if kind == "multiple_range":
            # Record the low end; the range itself stays in the text.
            return Metric(_to_float(groups["num"]), "x", approx=True)

        if kind == "multiple":
            return Metric(_to_float(groups["num"]), "x")

        if kind == "count_min":
            return Metric(_to_float(groups["num"]), "count", approx=True)

        if kind == "count":
            return Metric(_to_float(groups["num"]), "count")

    return None
