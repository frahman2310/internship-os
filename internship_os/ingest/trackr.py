"""Trackr adapter — wraps the existing Bun CLI rather than reimplementing it.

That CLI already handles parameter validation, exponential backoff on 429/5xx, and
structured errors on stderr. Porting it to Python would mean reimplementing three things
that already work and re-learning the same rate limits. We shell out and map the result.

ponytail: cross-repo path dependency via config. Vendor the CLI in the first time that
path breaks.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from ..config import settings
from .base import OpportunityDraft, SourceError

# Firms whose internships are quant/trading-shaped — the dual finance+tech sweet spot.
QUANT_FIRMS = {
    "jane street",
    "millennium management",
    "drw",
    "gsa capital",
    "da vinci trading",
    "marshall wace",
    "virtu financial",
    "citadel",
    "citadel securities",
    "point72",
    "hudson river trading",
    "aquatic capital management",
    "capula investment management",
    "squarepoint capital",
    "qube research technologies",
    "optiver",
    "imc trading",
    "two sigma",
    "btg pactual",
    "balyasny asset management",
    "g research",
    "jump trading",
    "xtx markets",
    "flow traders",
    "maven securities",
    "akuna capital",
    "wintermute",
}

def _tri(value: str | None) -> str:
    """Trackr writes these as 'Yes' | 'No' | 'Optional' | null."""
    return {"Yes": "yes", "No": "no", "Optional": "optional"}.get(value or "", "unknown")


class TrackrAdapter:
    key = "trackr"

    async def fetch(
        self,
        *,
        region: str = "UK",
        industry: str = "Finance",
        season: str = "2027",
        type_: str = "summer-internships",
        open_only: bool = False,
        max_pages: int = 40,
    ) -> list[dict]:
        """One API call per scope, using the CLI's --all flag.

        The API returns the whole season in a single response, so paginating meant
        re-fetching all 437 records once per page — which is exactly what the rate limiter
        punishes. One call, all results.
        """
        body = await self._page(region, industry, season, type_, open_only, page=1)
        return body.get("results", [])

    async def _page(
        self, region, industry, season, type_, open_only, page: int
    ) -> dict:
        args = [
            settings.bun_path,
            "run",
            str(settings.trackr_cli_path),
            "search",
            "-r", region,
            "-i", industry,
            "-s", season,
            "-t", type_,
            "--all",
            "--format", "json",
        ]
        if open_only:
            args.append("--open")

        # limit= raised well above asyncio's 64KiB default: a full season is ~500KB of
        # pretty-printed JSON, and the default silently truncates mid-document.
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=16 * 1024 * 1024,
        )
        out, err = await proc.communicate()

        if proc.returncode != 0:
            code, message = None, err.decode(errors="replace").strip()
            try:
                parsed = json.loads(message.splitlines()[-1])
                code, message = parsed.get("code"), parsed.get("error", message)
            except (ValueError, IndexError):
                pass
            raise SourceError(f"trackr CLI failed: {message}", code=code)

        try:
            return json.loads(out.decode())
        except ValueError as exc:
            raise SourceError(f"unparseable trackr output: {exc}") from exc

    def to_draft(
        self,
        raw: dict[str, Any],
        *,
        industry: str = "Finance",
        season: str = "2027",
        programme_type: str = "summer-internships",
        region: str = "UK",
    ) -> OpportunityDraft:
        company = raw.get("company") or "Unknown"
        categories = [c.strip() for c in (raw.get("category") or "").split(",") if c.strip()]
        locations = [
            loc.strip() for loc in (raw.get("location") or "").split(",") if loc.strip()
        ]
        visa = raw.get("sponsorsVisa")
        sponsors = "yes" if visa is True else "no" if visa is False else "unknown"

        return OpportunityDraft(
            company_name=company,
            title=raw.get("title") or "(untitled)",
            source_adapter=self.key,
            source_ref=raw.get("id"),
            sponsors_visa=sponsors,
            url=raw.get("url"),
            apply_url=raw.get("url"),
            season=season,
            region=region,
            industry=industry,
            programme_type=programme_type,
            locations=locations,
            categories=categories,
            track="tech" if industry.lower() == "tech" else "finance",
            is_quant=company.lower().strip() in QUANT_FIRMS,
            opening_date=raw.get("date"),
            closing_date=raw.get("deadline"),
            rolling=bool(raw.get("rolling")),
            process_raw=raw.get("process"),
            requires_cv=bool(raw.get("cv")),
            requires_cover_letter=_tri(raw.get("coverLetter")),
            requires_written_answers=_tri(raw.get("writtenAnswers")),
            acceptance_rate=raw.get("acceptanceRate"),
            conversion_rate=raw.get("conversionRate"),
            eligibility_raw=raw.get("eligibility"),
            notes_raw=raw.get("notes"),
            raw_payload=raw,
        )
