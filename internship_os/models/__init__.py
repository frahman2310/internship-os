"""All models, imported so Alembic's autogenerate and Base.metadata see them."""

from __future__ import annotations

from ..db import Base, SessionLocal
from .application import (
    RESERVED_FOR_HUMAN,
    Application,
    ApplicationAnswer,
    ApplicationEvent,
    Approval,
    AtsPlatform,
    Experiment,
    Outcome,
)
from .base import (
    EPISTEMIC_CLASSES,
    GATE_VERDICTS,
    GROUNDING,
    OPPORTUNITY_STATES,
    TERMINAL_STATES,
    today,
    utcnow,
)
from .candidate import (
    Candidate,
    CandidateLanguage,
    EvidenceItem,
    EvidenceSkill,
    Experience,
    Skill,
)
from .company import TOPIC_HALF_LIFE_MONTHS, Company, CompanyIntelligence
from .document import Document, DocumentClaim, DocumentVersion
from .infra import Job, LlmCall
from .invariants import InvariantViolation
from .invariants import install as install_invariants
from .opportunity import FitAssessment, Opportunity, OpportunityStateEvent, Strategy
from .research import (
    ClaimEvidence,
    Contradiction,
    ResearchClaim,
    ResearchExcerpt,
    ResearchQuestion,
    ResearchRun,
    ResearchSource,
)

# Installed at import time so any session created anywhere is covered.
install_invariants(SessionLocal)

__all__ = [
    "Base",
    "Candidate",
    "CandidateLanguage",
    "Experience",
    "EvidenceItem",
    "Skill",
    "EvidenceSkill",
    "Company",
    "CompanyIntelligence",
    "TOPIC_HALF_LIFE_MONTHS",
    "Opportunity",
    "OpportunityStateEvent",
    "FitAssessment",
    "Strategy",
    "ResearchRun",
    "ResearchQuestion",
    "ResearchSource",
    "ResearchExcerpt",
    "ResearchClaim",
    "ClaimEvidence",
    "Contradiction",
    "Document",
    "DocumentVersion",
    "DocumentClaim",
    "AtsPlatform",
    "Application",
    "ApplicationAnswer",
    "ApplicationEvent",
    "Approval",
    "Outcome",
    "Experiment",
    "RESERVED_FOR_HUMAN",
    "Job",
    "LlmCall",
    "InvariantViolation",
    "install_invariants",
    "OPPORTUNITY_STATES",
    "TERMINAL_STATES",
    "EPISTEMIC_CLASSES",
    "GROUNDING",
    "GATE_VERDICTS",
    "utcnow",
    "today",
]
