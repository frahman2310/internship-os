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
    return await call_schema(
        f"Posting for {role} at {company}. Extract ONLY what this page states.\n\n"
        f"{page_text[:14000]}\n\n"
        "List every requirement, every document requested, and every free-text question "
        "asked. Mark a requirement hard only if the page says it disqualifies. Invent "
        "nothing: if the page is silent on something, leave it out.",
        Requirements, system="You extract application requirements from job postings.",
        purpose="intel:requirements", temperature=0.1, max_tokens=2600,
    )


def check_gates(requirements: Requirements, *, graduation_year: int = 2028,
                needs_sponsorship: bool = True) -> list[Gate]:
    """Screen the hard requirements against fixed facts about the candidate.

    Only the two that are mechanically checkable and most often disqualifying. Everything
    else stays `unknown` rather than being guessed — a false `pass` here means a wasted
    application, and a false `fail` means a missed one.
    """
    gates: list[Gate] = []
    for req in requirements.requirements:
        if not req.hard:
            continue
        low = req.text.lower()
        if "graduat" in low or "final year" in low or "penultimate" in low:
            if str(graduation_year) in low:
                gates.append(Gate(req.text, "pass", f"graduation {graduation_year} named"))
            elif "penultimate" in low:
                gates.append(Gate(req.text, "pass", "penultimate year matches 2026/27"))
            else:
                gates.append(Gate(req.text, "unknown", "graduation window not stated clearly"))
        elif "sponsor" in low or "right to work" in low or "visa" in low:
            if "cannot sponsor" in low or "unable to sponsor" in low or "no sponsorship" in low:
                gates.append(Gate(req.text, "fail" if needs_sponsorship else "pass",
                                  "employer states it does not sponsor"))
            else:
                gates.append(Gate(req.text, "unknown", "sponsorship stance unclear"))
        else:
            gates.append(Gate(req.text, "unknown", "needs a human read"))
    return gates
