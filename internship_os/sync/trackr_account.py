"""Write your application status back into your own Trackr account.

Trackr's status feature is authenticated with a **bearer token held in localStorage**
(`trackrAccessToken`, refreshed via `/users/refresh` with `trackrRefreshToken`) — NOT the
cookie session originally assumed. The write endpoints were recovered from the chunked
Angular bundle:

    POST/PUT  https://api.the-trackr.com/programmes/update-status        (single programme)
    POST/PUT  https://api.the-trackr.com/programmes/update-group-status  (a group)

Both are marked `authRequired` in the app's route table. The exact verb and payload shape
still come from a live capture rather than a guess — it is
*discovered* from your own action:

    1. `trackr login`   — a headed browser opens; YOU type your credentials. The system
                          never sees, requests or stores your password. The session lives in
                          a browser profile directory on disk, exactly like a normal login.
    2. `trackr capture` — you change one status in the UI while request recording is on.
                          Whatever call the app makes is saved as a replayable recipe.
    3. `trackr push`    — replays that call for other programmes, using the saved session.

If the recipe ever stops working (they redeploy, the route changes), the fallback is to
drive the UI in the same headed browser rather than to reverse-engineer harder.

Personal use, your own account, your own data, one status at a time.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import settings

APP_URL = "https://app.the-trackr.com"
API_HOST = "api.the-trackr.com"

# Local pipeline state -> the vocabulary Trackr's own UI uses.
STATUS_MAP = {
    "DISCOVERED": "Not Applied",
    # Trackr's own vocabulary gained "Interested" — everything past triage but before a
    # submission is exactly that, and it is what the user sets by hand.
    "QUALIFIED": "Interested",
    "RESEARCHING": "Interested",
    "READY_TO_APPLY": "Interested",
    "DRAFTING": "Interested",
    "AWAITING_REVIEW": "Interested",
    "APPROVED": "Interested",
    "APPLYING": "Interested",
    "SUBMITTED": "Applied",
    "ASSESSMENT": "Interview",
    "INTERVIEW": "Interview",
    "OFFER": "Offer",
    "REJECTED": "Rejected",
    "REJECTED_BY_GATE": "Not Applied",
    "WITHDRAWN": "Not Applied",
    "EXPIRED": "Not Applied",
}


def profile_dir() -> Path:
    path = settings.var_dir / "browser-profile"
    path.mkdir(parents=True, exist_ok=True)
    return path


def recipe_path() -> Path:
    return settings.var_dir / "trackr_status_recipe.json"


@dataclass
class StatusRecipe:
    """A recorded status-change request, generalised into a template."""

    method: str
    url: str
    headers: dict[str, str]
    body: Any | None
    observed_at: str
    note: str = ""

    def to_json(self) -> dict:
        return {
            "method": self.method,
            "url": self.url,
            "headers": self.headers,
            "body": self.body,
            "observed_at": self.observed_at,
            "note": self.note,
        }

    @classmethod
    def load(cls) -> "StatusRecipe | None":
        path = recipe_path()
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        return cls(**data)

    def save(self) -> Path:
        path = recipe_path()
        path.write_text(json.dumps(self.to_json(), indent=2))
        return path


PROGRAMME_TOKEN = "{{PROGRAMME_ID}}"
STATUS_TOKEN = "{{STATUS}}"


def templatise(
    recipe: StatusRecipe, known_programme_ids: set[str]
) -> tuple[StatusRecipe, dict[str, str]]:
    """Turn one recorded request into a reusable template.

    Rather than asking which programme you clicked, find it: scan the URL and body for any
    Trackr programme id we already hold and for any status label the app uses, then replace
    those literals with tokens. If neither is found the recipe stays literal and `push`
    refuses — better than sending a request that silently updates the wrong row.
    """
    blob_url = recipe.url
    blob_body = json.dumps(recipe.body) if recipe.body is not None else ""
    found: dict[str, str] = {}

    for pid in sorted(known_programme_ids, key=len, reverse=True):
        if pid and (pid in blob_url or pid in blob_body):
            found["programme_id"] = pid
            blob_url = blob_url.replace(pid, PROGRAMME_TOKEN)
            blob_body = blob_body.replace(pid, PROGRAMME_TOKEN)
            break

    for label in sorted(set(STATUS_MAP.values()), key=len, reverse=True):
        if label in blob_body or label in blob_url:
            found["status"] = label
            blob_url = blob_url.replace(label, STATUS_TOKEN)
            blob_body = blob_body.replace(label, STATUS_TOKEN)
            break

    templated = StatusRecipe(
        method=recipe.method,
        url=blob_url,
        headers=recipe.headers,
        body=json.loads(blob_body) if blob_body else None,
        observed_at=recipe.observed_at,
        note=recipe.note,
    )
    return templated, found


def render(recipe: StatusRecipe, programme_id: str, status_label: str) -> tuple[str, Any]:
    """Fill a template for one programme. Raises if the template was never parameterised."""
    body_json = json.dumps(recipe.body) if recipe.body is not None else ""
    if PROGRAMME_TOKEN not in recipe.url and PROGRAMME_TOKEN not in body_json:
        raise ValueError(
            "the captured request has no programme placeholder — re-run `trackr capture` "
            "and change the status of a programme this system already knows about"
        )
    # A capture made before a label was in STATUS_MAP leaves that label baked in as a
    # literal. Replaying it would then write the captured status onto every programme,
    # silently and wrongly. Refuse rather than send.
    if STATUS_TOKEN not in recipe.url and STATUS_TOKEN not in body_json:
        for known in sorted(set(STATUS_MAP.values()), key=len, reverse=True):
            if known in recipe.url or known in body_json:
                if known != status_label:
                    raise ValueError(
                        f"the captured request has {known!r} baked in and cannot be "
                        f"rewritten to {status_label!r} — re-run `trackr capture`"
                    )
                break
    url = recipe.url.replace(PROGRAMME_TOKEN, programme_id).replace(
        STATUS_TOKEN, status_label
    )
    body = (
        json.loads(
            body_json.replace(PROGRAMME_TOKEN, programme_id).replace(
                STATUS_TOKEN, status_label
            )
        )
        if body_json
        else None
    )
    return url, body


async def push_status(programme_id: str, status_label: str) -> tuple[int, str]:
    """Replay the captured call for one programme, reusing the browser session's cookies."""
    from playwright.async_api import async_playwright

    recipe = StatusRecipe.load()
    if recipe is None:
        raise FileNotFoundError("no captured recipe — run `trackr capture` first")
    url, body = render(recipe, programme_id, status_label)

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(profile_dir()), headless=True
        )
        try:
            response = await context.request.fetch(
                url,
                method=recipe.method,
                data=body,
                headers={k: v for k, v in recipe.headers.items() if k.lower() != "content-length"},
            )
            return response.status, (await response.text())[:300]
        finally:
            await context.close()


async def open_login() -> bool:
    """Open Trackr in a persistent, headed browser and wait while you sign in.

    Deliberately hands control to you: no credential fields are read or filled by the
    system. Returns True if a logged-in session appears to exist when the browser closes.
    """
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(profile_dir()), headless=False, viewport={"width": 1400, "height": 900}
        )
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(f"{APP_URL}/account/login", wait_until="domcontentloaded")
        print(
            "\n  A browser window is open at Trackr's login page.\n"
            "  Sign in yourself — this system never reads or stores your password.\n"
            "  When you are logged in, close the window to continue.\n"
        )
        try:
            await page.wait_for_event("close", timeout=600_000)
        except Exception:
            pass
        cookies = await context.cookies()
        await context.close()
        return any(API_HOST in (c.get("domain") or "") or "trackr" in (c.get("domain") or "")
                   for c in cookies)


async def capture_status_call(timeout_ms: int = 300_000) -> StatusRecipe | None:
    """Record the request Trackr makes when YOU change a status in the UI."""
    from datetime import UTC, datetime

    from playwright.async_api import async_playwright

    captured: list[StatusRecipe] = []

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(profile_dir()), headless=False, viewport={"width": 1400, "height": 900}
        )
        page = context.pages[0] if context.pages else await context.new_page()

        async def on_request(request):
            if API_HOST not in request.url:
                return
            if request.method.upper() not in {"POST", "PUT", "PATCH", "DELETE"}:
                return
            body: Any | None = None
            try:
                body = request.post_data_json
            except Exception:
                body = request.post_data
            captured.append(
                StatusRecipe(
                    method=request.method.upper(),
                    url=request.url,
                    headers={
                        k: v
                        for k, v in request.headers.items()
                        if k.lower() in {"content-type", "accept", "origin", "referer"}
                    },
                    body=body,
                    observed_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    note="captured from a status change made by the account owner",
                )
            )

        page.on("request", on_request)
        await page.goto(f"{APP_URL}/uk-finance/summer-internships", wait_until="domcontentloaded")
        print(
            "\n  Browser open. Change the status of ONE programme using the 'My Status'\n"
            "  dropdown. I will record the request the app sends, then you can close the\n"
            "  window.\n"
        )
        try:
            await page.wait_for_event("close", timeout=timeout_ms)
        except Exception:
            pass
        await context.close()

    return captured[-1] if captured else None


# --- automatic mirroring ------------------------------------------------------------

async def enqueue(session, opp, status_label: str) -> bool:
    """Push one status change to Trackr, recording the outcome on the opportunity.

    Called from the state machine after a successful local transition. Returns True only
    when Trackr accepted the write. Any failure is recorded and swallowed: the local state
    has already changed and is authoritative, so a mirror that is briefly behind is a far
    better outcome than a transition that fails because somebody else's server is down.
    """
    programme_id = str((opp.raw_payload or {}).get("id") or opp.source_ref or "").strip()
    if not programme_id:
        return False
    try:
        code, body = await push_status(programme_id, status_label)
    except Exception as exc:  # noqa: BLE001
        _record(opp, f"error {type(exc).__name__}", status_label)
        return False
    ok = 200 <= code < 300
    _record(opp, f"HTTP {code}" + ("" if ok else f" {body[:80]}"), status_label)
    return ok


def _record(opp, outcome: str, status_label: str) -> None:
    payload = dict(opp.raw_payload or {})
    payload["_trackr_sync"] = {
        "status": status_label,
        "outcome": outcome,
        "at": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).isoformat(timespec="seconds"),
    }
    opp.raw_payload = payload


def sync_state() -> dict:
    """Whether automatic mirroring is armed, for `doctor` and the CLI to report honestly."""
    recipe = StatusRecipe.load()
    return {
        "armed": recipe is not None,
        "recipe_path": str(recipe_path()),
        "observed_at": getattr(recipe, "observed_at", None),
        "reason": None if recipe else (
            "no status-write recipe captured yet — run `ios trackr capture`, change one "
            "status in the Trackr UI, and the exact call is recorded"),
    }
