"""Confidence is computed, never prompted.

Language models emit confidence numbers that clump around 0.85 regardless of evidence, so
the model writes the *rationale* and this module writes the *number*:

    confidence = tier_weight × independence × recency × directness

clamped to [0.05, 0.95] — nothing here is ever certain — and then capped by epistemic class.

The bias controls below are caps, not warnings. A claim about "what gets you hired" built
only on people who got hired cannot exceed weak-signal, no matter how many sources agree,
because they are all drawn from the same survivor pool.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

# Source tiers. Tier 1 includes the candidate's own recorded outcomes: for questions about
# a specific firm's process, what actually happened to you outranks any forum account.
TIER_WEIGHT = {1: 0.90, 2: 0.75, 3: 0.55, 4: 0.35, 5: 0.15}

CLASS_CAP = {"fact": 0.95, "inference": 0.70, "speculation": 0.35}

DIRECTNESS = {"direct_quote": 1.0, "synthesis": 0.8, "llm_inference": 0.5, "user_report": 1.0}

# Months before a claim is half as believable. Deadlines rot; culture does not.
TOPIC_HALF_LIFE = {
    "comp": 6,
    "recent_news": 6,
    "conversion": 12,
    "sponsorship_policy": 12,
    "process": 18,
    "interview_style": 18,
    "red_flags": 18,
    "culture": 36,
    "hiring_bar": 36,
    "tech_stack": 36,
    # Added with the application-intelligence topics. Market data is the shortest-lived
    # thing here: quarterly spread and issuance figures are the numbers the user would assert
    # in an interview, and a stale one is worse than none.
    "market_backdrop": 3,
    "deals": 6,
    "league_table": 12,
    "eligibility": 12,
    "sponsorship": 12,
    "deadline_behaviour": 12,
    "questions": 12,
    "verification": 12,
    "division_detail": 24,
    "success_factors": 24,
    "rejection_factors": 24,
}
DEFAULT_HALF_LIFE = 18

# Statements that assert causation. "X is why you get in" is a much stronger claim than
# "X appears among people who got in", and almost never supported by the evidence available.
CAUSAL = re.compile(
    r"\b(because|causes?|leads? to|results? in|improves? your chances|is why|"
    r"gets? you (in|hired)|guarantees?|ensures?)\b",
    re.I,
)

MAX_EXCERPTS_PER_DOMAIN = 2


@dataclass
class ClaimSignals:
    """Everything needed to score a claim, gathered by the orchestrator."""

    topic: str
    epistemic_class: str
    method: str
    statement: str
    source_tiers: list[int] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    as_of: str | None = None
    n_rejections_observed: int = 0
    self_outcome_fraction: float = 0.0
    disconfirming_search_ran: bool = False


@dataclass
class Scored:
    confidence: float
    epistemic_class: str
    bias_flags: list[str]
    n_supporting: int
    n_independent_domains: int
    rationale: str


def _months_since(as_of: str | None) -> float:
    if not as_of:
        return 0.0
    try:
        then = date.fromisoformat(as_of[:10])
    except ValueError:
        return 0.0
    days = (datetime.now(UTC).date() - then).days
    return max(0.0, days / 30.44)


def half_life(topic: str) -> int:
    """Topics may be namespaced per employer ("rothschild:eligibility") when one research
    run covers several firms. Match on the part after the colon, or an exact lookup would
    silently drop every namespaced claim to the default and let market data sit fresh for
    eighteen months."""
    key = topic.split(":", 1)[-1].strip().lower()
    return TOPIC_HALF_LIFE.get(key, TOPIC_HALF_LIFE.get(topic, DEFAULT_HALF_LIFE))


def stale_after(topic: str, as_of: str) -> str:
    """A claim is stale once a full half-life has passed; it is then flagged, never dropped."""
    months = half_life(topic)
    base = date.fromisoformat(as_of[:10])
    year, month = divmod(base.month - 1 + months, 12)
    return date(base.year + year, month + 1, min(base.day, 28)).isoformat()


def score(signals: ClaimSignals) -> Scored:
    flags: list[str] = []
    epistemic_class = signals.epistemic_class

    # --- correlation is not causation -------------------------------------------------
    if CAUSAL.search(signals.statement) and epistemic_class == "fact":
        if not any(t == 1 for t in signals.source_tiers):
            epistemic_class = "inference"
            flags.append("causal_language_downgraded")

    # --- availability: one prolific domain cannot manufacture independence ------------
    domain_counts: dict[str, int] = {}
    kept_tiers: list[int] = []
    for tier, domain in zip(signals.source_tiers, signals.domains, strict=False):
        if domain_counts.get(domain, 0) >= MAX_EXCERPTS_PER_DOMAIN:
            flags.append("availability_capped")
            continue
        domain_counts[domain] = domain_counts.get(domain, 0) + 1
        kept_tiers.append(tier)

    n_supporting = len(kept_tiers)
    n_domains = len(domain_counts)

    if not kept_tiers:
        return Scored(0.05, "speculation", flags + ["no_evidence"], 0, 0, "no evidence attached")

    best_tier = min(kept_tiers)  # lower number = stronger source
    tier_weight = TIER_WEIGHT.get(best_tier, 0.15)
    independence = min(1.30, 1 + 0.15 * (n_domains - 1))
    recency = 0.5 ** (_months_since(signals.as_of) / half_life(signals.topic))
    directness = DIRECTNESS.get(signals.method, 0.5)

    confidence = tier_weight * independence * recency * directness

    # --- survivorship: the dominant failure mode of internship research ---------------
    if _is_success_claim(signals.statement) and signals.n_rejections_observed == 0:
        flags.append("survivorship")
        confidence = min(confidence, 0.40)

    # --- self-anchoring: your own outcomes are tier 1 but n is tiny -------------------
    if signals.self_outcome_fraction > 0.5 and n_supporting < 5:
        flags.append("self_anchored")
        confidence = min(confidence, 0.60)

    # --- selection: forum venues are self-selecting ------------------------------------
    if any(t >= 3 for t in kept_tiers):
        flags.append("venue_selection")

    # --- small n ----------------------------------------------------------------------
    if n_supporting < 3:
        flags.append("small_n")

    # --- confirmation: a claim nobody tried to refute is weaker ------------------------
    if not signals.disconfirming_search_ran:
        flags.append("unrefuted")
        confidence *= 0.9

    confidence = min(confidence, CLASS_CAP.get(epistemic_class, 0.70))
    confidence = max(0.05, min(0.95, confidence))

    rationale = (
        f"tier {best_tier} ({tier_weight:.2f}) × independence {independence:.2f} "
        f"× recency {recency:.2f} × directness {directness:.2f}"
        + (f"; capped by {', '.join(flags)}" if flags else "")
    )
    return Scored(
        round(confidence, 3), epistemic_class, flags, n_supporting, n_domains, rationale
    )


_SUCCESS = re.compile(
    r"\b(successful candidates?|got (in|an offer)|who got|to get in|land(ed)? (the|a) "
    r"(role|internship|offer)|hired candidates?|what they look for)\b",
    re.I,
)


def _is_success_claim(statement: str) -> bool:
    return bool(_SUCCESS.search(statement))


def band(confidence: float) -> str:
    """How a number should be spoken about, so inference never reads like fact."""
    if confidence >= 0.75:
        return "well supported"
    if confidence >= 0.50:
        return "likely"
    if confidence >= 0.30:
        return "weak signal"
    return "speculative — do not act on"
