"""Retrieval: fetch pages, store bodies, assign source tiers, search the web key-lessly.

Bodies are stored on disk and hashed. That is what makes verbatim-or-discard possible:
an excerpt is only written if the quote literally appears in the stored text.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote_plus, urlparse

import httpx

from ...config import settings

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

# Tier by domain shape. Anything unrecognised is tier 3 — assumed to be a specialist or
# secondary source until something proves it is primary.
TIER_2_DOMAINS = {
    "ft.com", "reuters.com", "bloomberg.com", "economist.com", "wsj.com",
    "efinancialcareers.co.uk", "efinancialcareers.com", "thetimes.co.uk",
    "gov.uk", "prospects.ac.uk", "targetjobs.co.uk", "brightnetwork.co.uk",
}
TIER_4_DOMAINS = {
    "reddit.com", "www.reddit.com", "old.reddit.com", "glassdoor.com",
    "glassdoor.co.uk", "wallstreetoasis.com", "indeed.com", "fishbowlapp.com",
    "thestudentroom.co.uk", "quora.com",
}
# Freelance marketplaces. Gig copy from people SELLING application help is marketing, not
# evidence: every listing claims insider knowledge and none of it is checkable. Useful only
# as a hint about what the market believes, never citable on its own.
TIER_5_DOMAINS = {
    "fiverr.com", "www.fiverr.com", "upwork.com", "www.upwork.com",
    "peopleperhour.com", "freelancer.com",
}


@dataclass
class FetchedPage:
    url: str
    domain: str
    title: str | None
    text: str
    sha256: str
    path: Path
    tier: int
    is_primary: bool


def domain_of(url: str) -> str:
    return (urlparse(url).netloc or "").lower().removeprefix("www.")


def assign_tier(url: str, company_domains: set[str] | None = None) -> tuple[int, bool]:
    """(tier, is_primary). A company's own site is the primary source about itself."""
    dom = domain_of(url)
    if company_domains and any(dom.endswith(c) for c in company_domains if c):
        return 1, True
    if any(dom.endswith(d.removeprefix("www.")) for d in TIER_5_DOMAINS):
        return 5, False
    if any(dom.endswith(d.removeprefix("www.")) for d in TIER_4_DOMAINS):
        return 4, False
    if any(dom.endswith(d) for d in TIER_2_DOMAINS):
        return 2, False
    if dom.endswith(".gov.uk") or dom.endswith(".ac.uk"):
        return 2, False
    return 3, False


_TAG = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>", re.S | re.I)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
_ANY_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\r\f\v]+")
_BLANK = re.compile(r"\n{3,}")

_ENTITIES = {
    "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'",
    "&apos;": "'", "&nbsp;": " ", "&mdash;": "—", "&ndash;": "–", "&rsquo;": "'",
}


def html_to_text(html: str) -> str:
    body = _TAG.sub(" ", html)
    body = re.sub(r"</(p|div|li|h[1-6]|tr|section)>", "\n", body, flags=re.I)
    body = re.sub(r"<br\s*/?>", "\n", body, flags=re.I)
    body = _ANY_TAG.sub(" ", body)
    for entity, char in _ENTITIES.items():
        body = body.replace(entity, char)
    body = re.sub(r"&#x?[0-9a-fA-F]+;", " ", body)
    body = _WS.sub(" ", body)
    return _BLANK.sub("\n\n", body).strip()


def _cache_dir() -> Path:
    path = settings.var_dir / "pages"
    path.mkdir(parents=True, exist_ok=True)
    return path


async def fetch_page(
    url: str, *, company_domains: set[str] | None = None, timeout: float = 20.0
) -> FetchedPage | None:
    """Fetch, convert to text, store. Returns None on any failure — research continues."""
    try:
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=True, headers={"User-Agent": UA}
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            html = response.text
    except (httpx.HTTPError, UnicodeDecodeError):
        return None

    text = html_to_text(html)
    if len(text) < 200:
        return None

    title_match = _TITLE.search(html)
    digest = hashlib.sha256(text.encode()).hexdigest()
    path = _cache_dir() / f"{digest[:16]}.txt"
    if not path.exists():
        path.write_text(text, encoding="utf-8")

    tier, is_primary = assign_tier(url, company_domains)
    return FetchedPage(
        url=url,
        domain=domain_of(url),
        title=html_to_text(title_match.group(1))[:200] if title_match else None,
        text=text,
        sha256=digest,
        path=path,
        tier=tier,
        is_primary=is_primary,
    )


_DDG_RESULT = re.compile(
    r'<a[^>]+class="result__a"[^>]+href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>', re.S
)


async def web_search(query: str, *, limit: int = 6) -> list[dict]:
    """Key-less web search via DuckDuckGo's HTML endpoint.

    No API key to manage and no quota to exhaust, which matters for a tool meant to run
    every morning. Returns [] rather than raising when the shape changes — the research
    loop treats a dry search as an unanswered question, not a crash.
    """
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    try:
        async with httpx.AsyncClient(
            timeout=20.0, follow_redirects=True, headers={"User-Agent": UA}
        ) as client:
            response = await client.post(url)
            response.raise_for_status()
            html = response.text
    except httpx.HTTPError:
        return []

    results: list[dict] = []
    for match in _DDG_RESULT.finditer(html):
        href = match.group("href")
        if "duckduckgo.com/l/?uddg=" in href:
            from urllib.parse import parse_qs, unquote

            qs = parse_qs(urlparse(href).query)
            href = unquote(qs.get("uddg", [href])[0])
        if not href.startswith("http"):
            continue
        results.append({"url": href, "title": html_to_text(match.group("title"))[:200]})
        if len(results) >= limit:
            break
    return results
