"""Application intelligence: what actually gets someone into THIS employer.

Separate from company research because the question is different. Company research asks
what a firm is. This asks what its funnel rewards — the stages, the stated competencies,
what applicants report worked, and what got them cut.

Two honesty problems dominate, and both are handled as structure rather than warnings:

  * **Survivorship.** Every "how I got in" post is written by someone who got in. The
    confidence engine already caps a claim about success factors at 0.40 when no observed
    rejections back it, so these claims cannot masquerade as reliable.
  * **Marketplace copy.** Fiverr and Upwork listings from self-described ex-recruiters are
    advertising. They are tier 5 — never citable alone, and used only to surface a
    hypothesis worth checking against a better source.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import BaseModel, Field

from ..llm.schema_call import call_schema

# The eight questions that change how an application is written. Ordered by how much a
# wrong answer costs: eligibility first, because failing a gate makes the rest irrelevant.
INTEL_QUESTIONS = [
    ("eligibility", "What are {company}'s stated eligibility requirements for {role} — "
                    "degree discipline, graduation year, visa sponsorship, prior experience?"),
    ("process", "What are the assessment stages for {company} {role}, in order, and what "
                "does each stage actually test?"),
    ("hiring_bar", "What competencies or qualities does {company} explicitly say it looks "
                   "for in {role} applicants?"),
    ("deadline_behaviour", "Does {company} assess {role} applications on a rolling basis, "
                           "and how early do applicants report needing to apply?"),
    ("success_factors", "What do applicants who received offers from {company} for {role} "
                        "report about what made their application work?"),
    ("rejection_factors", "What do applicants rejected by {company} for {role} report about "
                          "where they were cut and why?"),
    ("recent_news", "What has {company} done in the last twelve months that a candidate "
                    "could credibly reference — deals, launches, strategy changes?"),
    ("culture", "How do current and former {company} interns describe the work and the "
                "environment?"),
]

# Where to look, per question. A generic web search for "what do Goldman interns say"
# returns careers-marketing; the site-scoped query reaches the forum where people actually
# talk. Tier is assigned on fetch by domain, so scoping the query does not inflate trust.
SOURCE_SCOPES = {
    "eligibility": ["{company} {role} eligibility requirements site:{domain}",
                    "{company} {role} application requirements"],
    "process": ["{company} {role} interview process stages",
                "{company} internship assessment centre site:wallstreetoasis.com",
                "{company} interview process site:reddit.com"],
    "hiring_bar": ["{company} graduate recruitment what we look for",
                   "{company} internship competencies"],
    "deadline_behaviour": ["{company} {role} rolling applications deadline",
                           "{company} internship apply early site:reddit.com"],
    "success_factors": ["{company} internship offer how I got in site:reddit.com",
                        "{company} summer analyst offer site:wallstreetoasis.com"],
    "rejection_factors": ["{company} internship rejected after site:reddit.com",
                          "{company} online test rejection site:thestudentroom.co.uk"],
    "recent_news": ["{company} news 2026", "{company} strategy announcement"],
    "culture": ["{company} intern experience site:reddit.com",
                "{company} internship review site:glassdoor.co.uk"],
}


def queries_for(topic: str, company: str, role: str, domain: str = "") -> list[str]:
    scopes = SOURCE_SCOPES.get(topic, ["{company} {role} " + topic])
    out = []
    for tpl in scopes:
        q = tpl.format(company=company, role=role, domain=domain or "")
        out.append(" ".join(q.replace("site:", "site:").split()).replace("site: ", ""))
    return out


def question_set(company: str, role: str) -> list[tuple[str, str]]:
    return [(topic, tpl.format(company=company, role=role)) for topic, tpl in INTEL_QUESTIONS]


class Requirement(BaseModel):
    text: str = Field(description="One stated requirement, quoted or closely paraphrased")
    kind: str = Field(description="eligibility | document | competency | logistics")
    hard: bool = Field(description="True if failing it disqualifies the application")


class Requirements(BaseModel):
    requirements: list[Requirement] = Field(default_factory=list)
    documents: list[str] = Field(default_factory=list, description="CV, cover letter, transcript…")
    questions: list[str] = Field(default_factory=list, description="Free-text questions asked")
    deadline_note: str = Field(default="", description="Anything stated about timing")


# Phrases that make a requirement disqualifying. The model under-calls `hard` — it marked
# "Candidates are required to be pursuing a degree completing between June 2027 and July
# 2028" as soft — and a missed hard gate is the expensive direction of that error: it lets
# an ineligible application consume a slot in a batch.
HARD_MARKERS = re.compile(
    r"(?i)(required to be|are required|must be|must have|minimum of|on track for a minimum|"
    r"must|limited to \d+ application|unable to apply|no deferral|"
    r"eligibility criteria|you will need to be|only accept)"
)


def harden(requirements: "Requirements") -> "Requirements":
    """Re-mark requirements the model called soft but whose wording disqualifies."""
    for req in requirements.requirements:
        if not req.hard and HARD_MARKERS.search(req.text):
            req.hard = True
    return requirements


@dataclass
class Gate:
    requirement: str
    verdict: str   # pass | fail | unknown
    reason: str


async def extract_requirements(page_text: str, company: str, role: str) -> Requirements:
    """Pull the application's actual requirements out of a fetched posting.

    Deliberately extractive: a requirement invented here would silently reshape the whole
    application. If the page does not state something, it does not appear.
    """
    result = await call_schema(
        f"Posting for {role} at {company}. Extract ONLY what this page states.\n\n"
        f"{page_text[:14000]}\n\n"
        "List every requirement, every document requested, and every free-text question "
        "asked. Mark a requirement hard only if the page says it disqualifies. Invent "
        "nothing: if the page is silent on something, leave it out.",
        Requirements, system="You extract application requirements from job postings.",
        purpose="intel:requirements", temperature=0.1, max_tokens=2600,
    )
    return harden(result)


def _years(text: str) -> list[int]:
    return [int(y) for y in re.findall(r"\b(20\d{2})\b", text)]


def check_gates(requirements: Requirements, *, graduation_year: int = 2028,
                classification: str = "2:1", needs_sponsorship: bool = True) -> list[Gate]:
    """Screen hard requirements against fixed facts about the candidate.

    Only checks what can be decided mechanically. Everything else stays `unknown` rather
    than guessed: a false `pass` wastes an application, a false `fail` loses one.
    """
    gates: list[Gate] = []
    for req in requirements.requirements:
        if not req.hard:
            continue
        low = req.text.lower()

        # Graduation window. Employers rarely say "graduation" — Bank of America says
        # "completion time frame between June 2027 and July 2028" — so match on the
        # concept and read the year range out of the sentence.
        if any(k in low for k in ("graduat", "completion", "final year", "penultimate",
                                  "degree with a completion", "expected to complete")):
            years = _years(req.text)
            if len(years) >= 2 and min(years) <= graduation_year <= max(years):
                gates.append(Gate(req.text, "pass",
                                  f"graduates {graduation_year}, inside {min(years)}–{max(years)}"))
            elif years and graduation_year not in years and len(years) == 1:
                gates.append(Gate(req.text, "fail",
                                  f"requires {years[0]}, candidate graduates {graduation_year}"))
            elif "penultimate" in low:
                gates.append(Gate(req.text, "pass", "penultimate year matches"))
            else:
                gates.append(Gate(req.text, "unknown", "no parseable completion window"))

        elif "2:1" in low or "2.1" in low or "upper second" in low or "first class" in low:
            gates.append(Gate(req.text, "pass",
                              f"on track for {classification} or above (First Class, Year 1)"))

        elif "sponsor" in low or "right to work" in low or "visa" in low or "work authoris" in low:
            # "Unrestricted and unlimited right to work in the UK" is the standard wording
            # that excludes visa holders, and it bites harder than a no-sponsorship line:
            # it rules out the internship itself, not just the graduate role after it. A
            # UK Student visa is time-limited and hour-capped in term, so it is neither
            # unrestricted nor unlimited on any plain reading.
            unrestricted = any(k in low for k in (
                "unrestricted and unlimited", "unrestricted right to work",
                "unlimited right to work", "permanent right to work",
                "without restriction", "no visa restrictions",
                "not require sponsorship", "without the need for sponsorship",
                "must already have the right to work",
            ))
            refuses = any(k in low for k in ("cannot sponsor", "unable to sponsor",
                                             "no sponsorship", "do not sponsor",
                                             "does not sponsor", "will not sponsor"))
            if unrestricted and needs_sponsorship:
                gates.append(Gate(req.text, "fail",
                                  "requires an unrestricted right to work; a UK Student visa "
                                  "is time-limited and hour-capped, so it does not qualify"))
            elif refuses:
                gates.append(Gate(req.text, "fail" if needs_sponsorship else "pass",
                                  "employer states it does not sponsor"))
            elif unrestricted:
                gates.append(Gate(req.text, "pass", "unrestricted right to work held"))
            else:
                gates.append(Gate(req.text, "unknown", "sponsorship stance unclear"))

        # Application caps are not pass/fail — they are a strategic constraint, and the
        # most expensive one to discover after submitting.
        elif re.search(r"limited to \d+ application|unable to apply to any additional", low):
            n = re.search(r"limited to (\d+)", low)
            gates.append(Gate(req.text, "unknown",
                              f"ONE-SHOT: capped at {n.group(1) if n else 'a limited number of'} "
                              "application(s) — choose the programme before applying"))

        elif "available to join" in low or "must be available" in low:
            gates.append(Gate(req.text, "pass", "summer 2027 availability assumed — confirm"))
        else:
            gates.append(Gate(req.text, "unknown", "needs a human read"))
    return gates
