"""Workday's public CXS endpoint.

Workday renders its job pages client-side from a JSON endpoint that needs no login,
the same trick that already works for Greenhouse, Lever and Workable. It does NOT
expose the application questionnaire, which lives behind the candidate session, but it
does give the full job description, the requisition id, the real posting dates and
`canApply`. That turns "open the tab so I can read it" into "the description is already
read and I know whether a questionnaire is coming".

Two URL shapes in the wild:
    https://<tenant>.wd12.myworkdayjobs.com/en-US/<site>/job/<Title>_<REQ>
(some tenants serve the same page under /details/ instead of /job/; CXS always wants /job/)
    https://wd1.myworkdaysite.com/en-US/recruiting/<tenant>/<site>/job/<Title>_<REQ>
both map to  .../wday/cxs/<tenant>/<site>/job/<Title>_<REQ>
"""

from __future__ import annotations

import html
import json
import re
import urllib.request

UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

SELF_HOSTED = re.compile(
    r"https?://(?P<tenant>[a-z0-9-]+)\.(?P<wd>wd\d+)\.myworkdayjobs\.com/"
    r"(?:[a-zA-Z-]+/)?(?P<site>[^/]+)/(?:job|details)/(?P<path>[^?#]+)")
SITE_HOSTED = re.compile(
    r"https?://(?P<wd>wd\d+)\.myworkdaysite\.com/(?:[a-zA-Z-]+/)?recruiting/"
    r"(?P<tenant>[^/]+)/(?P<site>[^/]+)/(?:job|details)/(?P<path>[^?#]+)")


def cxs_url(apply_url: str) -> str | None:
    """The public JSON endpoint for a Workday posting, or None if not Workday."""
    m = SELF_HOSTED.search(apply_url or "")
    if m:
        d = m.groupdict()
        return (f"https://{d['tenant']}.{d['wd']}.myworkdayjobs.com/wday/cxs/"
                f"{d['tenant']}/{d['site']}/job/{d['path']}")
    m = SITE_HOSTED.search(apply_url or "")
    if m:
        d = m.groupdict()
        return (f"https://{d['wd']}.myworkdaysite.com/wday/cxs/"
                f"{d['tenant']}/{d['site']}/job/{d['path']}")
    return None


def _text(markup: str) -> str:
    t = re.sub(r"<(br|/p|/li|/div|/h\d)[^>]*>", "\n", markup or "")
    t = re.sub(r"<[^>]+>", "", t)
    return re.sub(r"\n{3,}", "\n\n", html.unescape(t)).strip()


def fetch(apply_url: str, timeout: int = 20) -> dict | None:
    """Job description, dates and application flags, with no login. None if unreachable."""
    url = cxs_url(apply_url)
    if not url:
        return None
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = json.load(r)
    except Exception:
        return None
    info = payload.get("jobPostingInfo") or {}
    if not info:
        return None
    return {
        "title": info.get("title"),
        "req_id": info.get("jobRequisitionId"),
        "location": info.get("location"),
        "posted_on": info.get("startDate"),
        "end_date": info.get("endDate"),
        "can_apply": info.get("canApply"),
        "remote": info.get("remoteType"),
        "description": _text(info.get("jobDescription", "")),
        "cxs_url": url,
    }
