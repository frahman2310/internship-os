"""Field values for browser-assisted form filling, and the fields that are never filled.

The boundary is the point of this module. Two categories are withheld regardless of what
the form asks:

  * **Legally sensitive identifiers** — date of birth, National Insurance, passport. These
    are identity documents; an autofill mistake here is not a typo, it is a false legal
    declaration on a form the user signs.
  * **Diversity monitoring** — protected characteristics. Voluntary, employer-scored
    separately, and nobody's answer but his.

Salary expectation is withheld too, for a plainer reason: it is a negotiating position, not
a fact about him.

`submit` is not here and never will be. This module produces values; clicking the final
button requires an Approval token and lives in ats/submit.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.candidate import Candidate, CandidateLanguage, Experience

RESERVED_FOR_HUMAN = {
    "date_of_birth", "national_insurance", "passport_number", "nhs_number",
    "ethnicity", "gender", "sexual_orientation", "religion", "disability",
    "socioeconomic_background", "free_school_meals", "parental_education",
    "criminal_record", "salary_expectation", "notice_period", "referee_contact",
}

# Label fragments that mark a field as reserved, matched case-insensitively against the
# form's own visible label. Deliberately broad: a false positive costs one manual entry,
# a false negative puts a passport number into a form unattended.
RESERVED_PATTERNS = re.compile(
    r"(?i)(date\s*of\s*birth|d\.?o\.?b\b|national\s*insurance|\bni\s*number|passport|"
    r"ethnic|gender|sex\b|sexual\s*orientation|religion|disab|neurodiver|"
    r"socio.?economic|free\s*school\s*meal|first\s*generation|parent.*(educat|occupation)|"
    r"criminal|conviction|salary|compensation\s*expect|notice\s*period|"
    r"visa\s*number|share\s*code|right\s*to\s*work\s*document)"
)


@dataclass
class FieldValue:
    key: str
    value: str
    confidence: str = "high"  # high = literal profile fact; review = derived or phrased


@dataclass
class AutofillPlan:
    values: dict[str, FieldValue] = field(default_factory=dict)
    reserved: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, str]:
        return {k: v.value for k, v in self.values.items()}

    def lookup(self, label: str) -> FieldValue | None:
        """Resolve a form's visible label to a value, or None if reserved/unknown."""
        if is_reserved(label):
            return None
        norm = re.sub(r"[^a-z]+", "_", label.lower()).strip("_")
        if norm in self.values:
            return self.values[norm]
        for key, val in self.values.items():
            if key in norm or norm in key:
                return val
        for key, aliases in ALIASES.items():
            if key in self.values and any(a in norm for a in aliases):
                return self.values[key]
        return None


ALIASES = {
    "first_name": ("forename", "given_name", "first"),
    "last_name": ("surname", "family_name", "last"),
    "full_name": ("name", "candidate_name", "applicant"),
    "email": ("e_mail", "email_address", "contact_email"),
    "phone": ("telephone", "mobile", "phone_number", "contact_number"),
    "university": ("institution", "school", "college", "university_name"),
    "degree": ("course", "programme", "degree_title", "subject", "field_of_study"),
    "graduation_date": ("expected_graduation", "completion", "end_date", "graduation"),
    "grade": ("gpa", "classification", "predicted_grade", "academic_result"),
    "linkedin": ("linkedin_url", "linkedin_profile"),
    "github": ("github_url", "portfolio", "website", "personal_site"),
    "work_rights": ("right_to_work", "work_authorisation", "sponsorship", "eligible_to_work"),
    "city": ("town", "location", "current_location"),
    "nationality": ("citizenship", "country_of_citizenship"),
}


def is_reserved(label: str) -> bool:
    key = label.strip().lower().replace(" ", "_")
    return key in RESERVED_FOR_HUMAN or bool(RESERVED_PATTERNS.search(label))


async def build_plan(session: AsyncSession) -> AutofillPlan:
    candidate = (await session.execute(select(Candidate))).scalars().first()
    if candidate is None:
        raise ValueError("no candidate imported — run `profile import` first")

    parts = (candidate.full_name or "").split()
    plan = AutofillPlan()

    def put(key: str, value: str | None, confidence: str = "high") -> None:
        if value:
            plan.values[key] = FieldValue(key, str(value), confidence)

    put("full_name", candidate.full_name)
    put("first_name", parts[0] if parts else None)
    put("last_name", parts[-1] if len(parts) > 1 else None)
    put("email", candidate.email)
    put("phone", candidate.phone)
    put("linkedin", candidate.linkedin_url)
    put("github", candidate.github_url)
    put("city", (candidate.location_term or "").split(",")[0] or None)
    put("nationality", candidate.nationality)

    education = (await session.execute(
        select(Experience).where(
            Experience.candidate_id == candidate.id, Experience.kind == "education"
        ).order_by(Experience.start_date.desc())
    )).scalars().all()
    if education:
        top = education[0]
        put("university", top.organisation)
        put("degree", top.title)
        put("graduation_date", top.end_date)
        put("education_start", top.start_date)

    work = (await session.execute(
        select(Experience).where(
            Experience.candidate_id == candidate.id, Experience.kind == "work"
        ).order_by(Experience.start_date.desc())
    )).scalars().all()
    for i, exp in enumerate(work[:5], 1):
        put(f"employer_{i}", exp.organisation)
        put(f"job_title_{i}", exp.title)
        put(f"job_start_{i}", exp.start_date)
        put(f"job_end_{i}", exp.end_date)

    langs = (await session.execute(
        select(CandidateLanguage).where(CandidateLanguage.candidate_id == candidate.id)
    )).scalars().all()
    if langs:
        put("languages", ", ".join(f"{x.language} ({x.level})" for x in langs))

    # Derived, so flagged for review: the right phrasing depends on how the employer asks,
    # and getting it wrong on a visa question is costly.
    if candidate.visa_status or candidate.nationality:
        put("work_rights",
            "Currently hold a UK Student visa; will require sponsorship for full-time "
            "employment after graduation.", confidence="review")

    plan.reserved = sorted(RESERVED_FOR_HUMAN)
    return plan
