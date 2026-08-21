"""Parse the candidate profile markdown into structured, source-located records.

Structural parsing, no LLM. The headings and tables are regular, and a parser that can be
diffed beats one that can hallucinate — which matters more here than anywhere else, because
`evidence_items` is the whitelist every generated document is checked against.

Every evidence item records the file and line it came from, so `profile verify` can prove
that nothing in the database was invented.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.base import utcnow
from ..models.candidate import (
    Candidate,
    CandidateLanguage,
    EvidenceItem,
    EvidenceSkill,
    Experience,
    Skill,
)
from .metrics import extract_metric

_MONTHS = {
    m: i
    for i, m in enumerate(
        "jan feb mar apr may jun jul aug sep oct nov dec".split(), start=1
    )
}
_DATE_RANGE = re.compile(
    r"(?P<m1>[A-Za-z]{3})[a-z]*\.?\s*(?P<y1>\d{4})?\s*[–—\-]{1,2}\s*"
    r"(?:(?P<m2>[A-Za-z]{3})[a-z]*\.?\s*)?(?P<y2>\d{4}|Present)",
    re.I,
)
_SINGLE_DATE = re.compile(r"^(?P<m>[A-Za-z]{3})[a-z]*\.?\s*(?P<y>\d{4})$")
_TRAILING_PAREN = re.compile(r"\(([^()]*)\)\s*$")


def _clean(text: str) -> str:
    """Strip markdown emphasis and inline comments; collapse whitespace."""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"\*\*|__|`", "", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_for_match(text: str) -> str:
    """Aggressive fold used to prove an evidence item still exists in its source line."""
    return re.sub(r"[^a-z0-9]+", "", _clean(text).lower())


def _parse_dates(raw: str) -> tuple[str | None, str | None, bool]:
    """'Jul 2026 – Aug 2026' -> ('2026-07', '2026-08', False). 'Apr 2026' -> same twice."""
    raw = raw.strip()
    single = _SINGLE_DATE.match(raw)
    if single:
        month = _MONTHS.get(single["m"][:3].lower())
        stamp = f"{single['y']}-{month:02d}" if month else None
        return stamp, stamp, False

    m = _DATE_RANGE.search(raw)
    if not m:
        return None, None, False
    m1 = _MONTHS.get((m["m1"] or "")[:3].lower())
    y2 = m["y2"]
    is_current = bool(y2 and y2.lower() == "present")
    y1 = m["y1"] or (None if is_current else y2)
    start = f"{y1}-{m1:02d}" if (y1 and m1) else (y1 or None)
    if is_current:
        return start, None, True
    m2 = _MONTHS.get((m["m2"] or m["m1"] or "")[:3].lower())
    end = f"{y2}-{m2:02d}" if (y2 and m2) else y2
    return start, end, False


@dataclass
class ParsedProfile:
    identity: dict[str, str] = field(default_factory=dict)
    languages: list[dict] = field(default_factory=list)
    experiences: list[dict] = field(default_factory=list)
    skills: list[dict] = field(default_factory=list)
    source_file: str = ""
    source_sha256: str = ""

    @property
    def evidence_count(self) -> int:
        return sum(len(e["bullets"]) for e in self.experiences)


def parse_profile_markdown(path: Path) -> ParsedProfile:
    raw = path.read_text(encoding="utf-8")
    lines = raw.splitlines()
    parsed = ParsedProfile(
        source_file=str(path), source_sha256=hashlib.sha256(raw.encode()).hexdigest()
    )

    section = ""
    subsection = ""
    current: dict | None = None

    for idx, line in enumerate(lines, start=1):
        stripped = line.strip()

        if stripped.startswith("## "):
            section, subsection, current = stripped[3:].strip(), "", None
            continue
        if stripped.startswith("### "):
            heading = stripped[4:].strip()
            if section == "Professional Experience":
                current = _parse_experience_heading(heading, idx)
                if current:
                    parsed.experiences.append(current)
            else:
                subsection, current = heading, None
            continue

        if not stripped:
            continue

        # --- Identity: "- **Key:** value"
        if section == "Identity" and stripped.startswith("- **"):
            m = re.match(r"-\s+\*\*(?P<key>[^:*]+):?\*\*:?\s*(?P<val>.*)", stripped)
            if m:
                parsed.identity[m["key"].strip().lower()] = _clean(m["val"])
            continue

        # --- Language / education tables
        if stripped.startswith("|") and "---" not in stripped:
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if section == "Identity" and subsection == "Languages":
                if cells and cells[0].lower() != "language":
                    parsed.languages.append(
                        {"language": cells[0], "level": cells[1] if len(cells) > 1 else ""}
                    )
            elif section == "Education":
                if cells and cells[0].lower() != "degree" and len(cells) >= 3:
                    start, end, _ = _parse_dates(cells[1])
                    parsed.experiences.append(
                        {
                            "kind": "education",
                            "organisation": _clean(cells[2]),
                            "title": _clean(cells[0]),
                            "location": None,
                            "start_date": start,
                            "end_date": end,
                            "is_current": False,
                            "line": idx,
                            "bullets": (
                                [{"text": _clean(cells[3]), "line": idx}]
                                if len(cells) > 3 and cells[3].strip()
                                else []
                            ),
                        }
                    )
            continue

        # --- Experience body: location line, then bullets
        if section == "Professional Experience" and current is not None:
            if stripped.startswith("- "):
                current["bullets"].append({"text": _clean(stripped[2:]), "line": idx})
            elif not current["location"] and not stripped.startswith("#"):
                current["location"] = _clean(stripped)
            continue

        # --- Projects / awards / leadership: top-level bullets become experiences
        if section in {"Independent Projects", "Awards", "Leadership & Extracurricular"}:
            if line.startswith("- "):
                kind = {
                    "Independent Projects": "project",
                    "Awards": "award",
                    "Leadership & Extracurricular": "leadership",
                }[section]
                current = _parse_freeform_bullet(_clean(stripped[2:]), kind, idx)
                parsed.experiences.append(current)
            elif line.startswith("  ") and stripped.startswith("- ") and current is not None:
                current["bullets"].append({"text": _clean(stripped[2:]), "line": idx})
            continue

        # --- Technical skills
        if section == "Technical Skills" and stripped.startswith("- "):
            body = stripped[2:]
            m = re.match(r"\*\*(?P<cat>[^:*]+):?\*\*:?\s*(?P<items>.*)", body)
            family = _skill_family(subsection, m["cat"] if m else "")
            items = (m["items"] if m else body)
            for item in re.split(r"[;,]", items):
                name = _clean(item)
                if name and len(name) < 60:
                    parsed.skills.append({"name": name, "family": family, "line": idx})
            continue

    return parsed


def _parse_experience_heading(heading: str, line: int) -> dict | None:
    """'Advisory Intern - Grant Thornton Pakistan (Jul 2026 – Aug 2026)' -> parts.

    The trailing parenthetical is the date range; the organisation is whatever follows the
    LAST ' - ', because titles themselves contain em-dashes and commas.
    """
    dates = ""
    m = _TRAILING_PAREN.search(heading)
    if m and re.search(r"\d{4}", m.group(1)):
        dates = m.group(1)
        heading = heading[: m.start()].strip()

    if " - " in heading:
        title, organisation = heading.rsplit(" - ", 1)
    else:
        title, organisation = "", heading

    start, end, is_current = _parse_dates(dates)
    return {
        "kind": "work",
        "organisation": _clean(organisation),
        "title": _clean(title) or None,
        "location": None,
        "start_date": start,
        "end_date": end,
        "is_current": is_current,
        "line": line,
        "bullets": [],
    }


def _parse_freeform_bullet(text: str, kind: str, line: int) -> dict:
    """Projects/awards are '- **Name** — description'; take the bolded lead as the name."""
    name = text.split("(")[0].split("—")[0].split(":")[0].strip(" *·-")
    return {
        "kind": kind,
        "organisation": name[:80] or text[:80],
        "title": None,
        "location": None,
        "start_date": None,
        "end_date": None,
        "is_current": False,
        "line": line,
        "bullets": [{"text": text, "line": line}],
    }


def _skill_family(subsection: str, category: str) -> str:
    blob = f"{subsection} {category}".lower()
    if any(k in blob for k in ("finance", "valuation", "quantitative")):
        return "finance"
    if "language" in blob:
        return "backend" if "programming" in blob else "backend"
    for key, family in (
        ("backend", "backend"),
        ("frontend", "frontend"),
        ("infra", "infra"),
        ("devops", "infra"),
        ("ai", "backend"),
        ("pattern", "backend"),
        ("software", "tool"),
        ("tool", "tool"),
    ):
        if key in blob:
            return family
    return "tool"


def canonical_skill(name: str) -> str:
    return re.sub(r"[^a-z0-9+#.]+", "", name.lower())


# --------------------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------------------


@dataclass
class ImportReport:
    created_experiences: int = 0
    created_evidence: int = 0
    created_skills: int = 0
    unchanged: int = 0
    source_bullets: int = 0


async def import_profile(
    session: AsyncSession, parsed: ParsedProfile, *, dry_run: bool = False
) -> ImportReport:
    """Idempotent: re-running against unchanged source produces no new rows."""
    report = ImportReport(source_bullets=parsed.evidence_count)

    candidate = (await session.execute(select(Candidate))).scalars().first()
    if candidate is None:
        candidate = Candidate(full_name=parsed.identity.get("name", "Unknown"))
        session.add(candidate)
        await session.flush()

    ident = parsed.identity
    candidate.full_name = ident.get("name", candidate.full_name)
    candidate.email = ident.get("email") or candidate.email
    candidate.phone = ident.get("phone") or candidate.phone
    candidate.location_term = ident.get("location") or candidate.location_term
    candidate.linkedin_url = ident.get("linkedin") or candidate.linkedin_url
    candidate.github_url = ident.get("github") or candidate.github_url
    candidate.work_rights_note = ident.get("constraints") or candidate.work_rights_note
    if "student visa" in (ident.get("constraints") or "").lower():
        candidate.visa_status = "UK Student visa"
    candidate.source_file = parsed.source_file
    candidate.source_sha256 = parsed.source_sha256
    candidate.updated_at = utcnow()

    # languages
    existing_langs = {
        row.language
        for row in (
            await session.execute(
                select(CandidateLanguage).where(
                    CandidateLanguage.candidate_id == candidate.id
                )
            )
        ).scalars()
    }
    for lang in parsed.languages:
        if lang["language"] not in existing_langs:
            session.add(
                CandidateLanguage(
                    candidate_id=candidate.id,
                    language=lang["language"],
                    level=lang["level"],
                )
            )

    # experiences + evidence
    for entry in parsed.experiences:
        experience = (
            await session.execute(
                select(Experience).where(
                    Experience.candidate_id == candidate.id,
                    Experience.organisation == entry["organisation"],
                    Experience.kind == entry["kind"],
                )
            )
        ).scalars().first()
        if experience is None:
            experience = Experience(
                candidate_id=candidate.id,
                kind=entry["kind"],
                organisation=entry["organisation"],
                title=entry["title"],
                location=entry["location"],
                start_date=entry["start_date"],
                end_date=entry["end_date"],
                is_current=entry["is_current"],
                source_file=parsed.source_file,
                source_locator=f"L{entry['line']}",
            )
            session.add(experience)
            await session.flush()
            report.created_experiences += 1

        for bullet in entry["bullets"]:
            created = await _upsert_evidence(
                session, candidate, experience, bullet, parsed.source_file
            )
            report.created_evidence += int(created)
            report.unchanged += int(not created)

    # skills
    for skill in parsed.skills:
        canon = canonical_skill(skill["name"])
        if not canon:
            continue
        existing = (
            await session.execute(
                select(Skill).where(
                    Skill.candidate_id == candidate.id, Skill.canonical_name == canon
                )
            )
        ).scalars().first()
        if existing is None:
            session.add(
                Skill(
                    candidate_id=candidate.id,
                    name=skill["name"],
                    canonical_name=canon,
                    family=skill["family"],
                )
            )
            report.created_skills += 1

    if dry_run:
        await session.rollback()
    return report


async def _upsert_evidence(
    session: AsyncSession,
    candidate: Candidate,
    experience: Experience,
    bullet: dict,
    source_file: str,
) -> bool:
    norm = normalize_for_match(bullet["text"])
    if not norm:
        return False
    existing = (
        await session.execute(
            select(EvidenceItem).where(
                EvidenceItem.candidate_id == candidate.id,
                EvidenceItem.normalized_text == norm,
            )
        )
    ).scalars().first()
    if existing is not None:
        existing.last_confirmed_at = utcnow()
        return False

    metric = extract_metric(bullet["text"])
    session.add(
        EvidenceItem(
            candidate_id=candidate.id,
            experience_id=experience.id,
            text=bullet["text"],
            normalized_text=norm,
            category="metric" if metric else "achievement",
            metric_value=metric.value if metric else None,
            metric_unit=metric.unit if metric else None,
            metric_approx=metric.approx if metric else False,
            verified_by="profile_md",
            source_file=source_file,
            source_locator=f"L{bullet['line']}",
        )
    )
    return True


# --------------------------------------------------------------------------------------
# Consistency: report disagreements, never merge them
# --------------------------------------------------------------------------------------

_TEX_ENTRY = re.compile(r"\\entry\{([^}]*)\}\{([^}]*)\}\{([^}]*)\}")


def cross_check_master_cv(parsed: ParsedProfile, tex_path: Path) -> list[str]:
    """Compare organisations in the master CV against the profile markdown.

    Deliberately reports rather than merges: the two sources have drifted before, and a
    silent merge would pick a winner without telling anyone which.
    """
    if not tex_path.exists():
        return [f"master CV not found at {tex_path}"]

    tex = tex_path.read_text(encoding="utf-8")
    tex_orgs = {
        normalize_for_match(re.sub(r"\\[a-zA-Z]+|[{}]", "", m.group(1)))
        for m in _TEX_ENTRY.finditer(tex)
    }
    tex_orgs.discard("")

    # Projects included: the master CV lists them as \entry rows under Technical
    # Experience, so excluding them would report every project as a phantom mismatch.
    md_orgs = {
        normalize_for_match(e["organisation"])
        for e in parsed.experiences
        if e["kind"] in {"work", "education", "project"}
    }

    issues: list[str] = []
    for org in sorted(md_orgs - tex_orgs):
        issues.append(f"in profile markdown but not in master CV: {org}")
    for org in sorted(tex_orgs - md_orgs):
        issues.append(f"in master CV but not in profile markdown: {org}")
    return issues
