"""CV drafts as plain JSON on disk.

A draft is deliberately NOT a DocumentVersion: those are immutable, audited artefacts of
a finished build, and an editor that rewrote them in place would destroy the audit trail.
A draft is scratch space you own — it is seeded from the profile, edited freely, and only
becomes a DocumentVersion when it is compiled.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from sqlalchemy import select

from ..config import settings
from ..db import session_scope
from ..models.candidate import Candidate, CandidateLanguage, EvidenceItem, Experience, Skill

SECTION_ORDER = ["Education", "Work Experience", "Projects", "Leadership", "Additional"]
KIND_TO_SECTION = {
    "education": "Education",
    "work": "Work Experience",
    "project": "Projects",
    "leadership": "Leadership",
    "award": "Leadership",
}


def drafts_dir() -> Path:
    path = Path(settings.var_dir) / "cv_drafts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-") or "cv"


@dataclass
class Entry:
    org: str = ""
    role: str = ""
    location: str = ""
    dates: str = ""
    bullets: list[str] = field(default_factory=list)
    # Evidence ids the bullets came from, parallel to `bullets` where known. Kept so the
    # assistant can tell an edited bullet from one still matching its source.
    evidence_ids: list[int | None] = field(default_factory=list)


@dataclass
class Section:
    title: str
    entries: list[Entry] = field(default_factory=list)


@dataclass
class Draft:
    slug: str
    label: str
    name: str = ""
    contact: str = ""
    sections: list[Section] = field(default_factory=list)

    def to_json(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_json(data: dict) -> "Draft":
        return Draft(
            slug=data["slug"],
            label=data.get("label", data["slug"]),
            name=data.get("name", ""),
            contact=data.get("contact", ""),
            sections=[
                Section(
                    title=s["title"],
                    entries=[Entry(**e) for e in s.get("entries", [])],
                )
                for s in data.get("sections", [])
            ],
        )


def path_for(slug: str) -> Path:
    return drafts_dir() / f"{slugify(slug)}.json"


def load(slug: str) -> Draft | None:
    path = path_for(slug)
    if not path.exists():
        return None
    return Draft.from_json(json.loads(path.read_text()))


def save(draft: Draft) -> Path:
    path = path_for(draft.slug)
    path.write_text(json.dumps(draft.to_json(), indent=2, ensure_ascii=False))
    return path


def list_drafts() -> list[dict]:
    out = []
    for path in sorted(drafts_dir().glob("*.json")):
        try:
            data = json.loads(path.read_text())
            out.append({"slug": data["slug"], "label": data.get("label", data["slug"])})
        except (ValueError, OSError, KeyError):
            continue
    return out


async def seed_from_profile(slug: str, label: str) -> Draft:
    """Build a first draft out of everything in the profile. Nothing is filtered here —
    the point of the editor is that you decide what to cut, so it starts complete."""
    async with session_scope() as session:
        candidate = (await session.execute(select(Candidate))).scalars().first()
        if candidate is None:
            raise ValueError("no candidate imported — run `profile import` first")

        experiences = (await session.execute(
            select(Experience).where(Experience.candidate_id == candidate.id)
        )).scalars().all()
        evidence = (await session.execute(
            select(EvidenceItem).where(
                EvidenceItem.candidate_id == candidate.id, EvidenceItem.is_active.is_(True)
            )
        )).scalars().all()
        languages = (await session.execute(
            select(CandidateLanguage).where(CandidateLanguage.candidate_id == candidate.id)
        )).scalars().all()
        skills = (await session.execute(
            select(Skill).where(Skill.candidate_id == candidate.id)
        )).scalars().all()

        by_exp: dict[int | None, list[EvidenceItem]] = {}
        for item in evidence:
            by_exp.setdefault(item.experience_id, []).append(item)

        sections: dict[str, list[Entry]] = {t: [] for t in SECTION_ORDER}
        for exp in sorted(
            experiences, key=lambda e: (e.start_date or ""), reverse=True
        ):
            title = KIND_TO_SECTION.get(exp.kind)
            if not title:
                continue
            items = by_exp.get(exp.id, [])
            dates = " – ".join(x for x in (exp.start_date, exp.end_date) if x)
            sections[title].append(
                Entry(
                    org=exp.organisation,
                    role=exp.title or "",
                    location=exp.location or "",
                    dates=dates,
                    bullets=[i.text for i in items],
                    evidence_ids=[i.id for i in items],
                )
            )

        extras = []
        if skills:
            extras.append("Skills: " + ", ".join(s.name for s in skills[:14]))
        if languages:
            extras.append(
                "Languages: " + ", ".join(f"{l.language} ({l.level})" for l in languages)
            )
        if extras:
            sections["Additional"].append(Entry(bullets=extras, evidence_ids=[None] * len(extras)))

        contact = " | ".join(
            x for x in (candidate.email, candidate.phone, candidate.linkedin_url, candidate.github_url) if x
        )
        draft = Draft(
            slug=slugify(slug),
            label=label,
            name=candidate.full_name,
            contact=contact,
            sections=[Section(title=t, entries=sections[t]) for t in SECTION_ORDER if sections[t]],
        )
    save(draft)
    return draft
