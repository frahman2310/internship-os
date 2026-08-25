# internship-os — state of play

Durable snapshot so conversation context can be discarded without losing anything.
Last updated: 2026-08-22

## Owner
visa-sponsoring employers. Targeting **Summer 2027**, Finance **and** Tech.

## Two repos
| Path | What |
|---|---|
| `~/internship-os` | This system. Python + Typer CLI + SQLite. 114 tests passing. |
| `~/Jobs: CV/ai-job-search` | CVs, LaTeX templates, candidate profile, trackr-search CLI |

`ios` (at `~/.local/bin/ios`) runs the CLI from any directory.

## Built (phases 1–4)
- **Schema**: 31 tables, Alembic migrations round-trip.
- **Profile**: 19 experiences, 30 evidence items, 49 skills. `ios profile verify` proves
  every evidence item still matches its source file + line.
- **Ingestion**: 700 opportunities, 544 companies, 159 visa-sponsoring, 52 quant.
  Trackr adapter wraps the Bun CLI; dedup, change detection, ATS classification.
- **Research engine**: live. plan → recall → search → fetch → extract → claim →
  red-team → score. Confidence is computed, never prompted.
- **Trackr write-back**: code complete, needs one supervised capture.
- **Documents (phase 5)**: `ios doc build <opp>` → tailored one-page CV, compiled and
  verified. Bullets are *selected* from evidence, never written, so every `document_claim`
  is `grounded` by construction. Track (finance/tech) is inferred per opportunity.
- **Cover letters**: `ios doc cover <opp>`. The drafter cites ids per paragraph; the audit
  rejects a paragraph that cites nothing, cites an id never offered, or states a number
  absent from its sources (catches metric escalation, e.g. ~85% quietly becoming 90%).

## Two invariants (structural, not advisory)
1. A document version **cannot** reach `approved` while any generated span is
   `ungrounded`. Rephrasing evidence is allowed; inventing is unrepresentable.
2. Submission requires a single-use, expiring `Approval` token whose `scope_hash` is
   **recomputed against the live page** before clicking. `ats/submit.py` is the only
   module permitted to submit.

## Trackr write-back — endpoint found
Auth is a **bearer token in localStorage** (`trackrAccessToken`, refreshed at
`/users/refresh`), NOT the cookie session the plan assumed. Write endpoints, recovered
from the chunked Angular bundle at app.the-trackr.com and marked `authRequired`:

    api.the-trackr.com/programmes/update-status
    api.the-trackr.com/programmes/update-group-status

Still needed: one live capture to pin the verb and payload shape.

## Providers
- **Gemini**: live, `gemini/gemini-3.6-flash`. (2.5-flash is retired for new accounts.)
  Key format `AQ.…` is current; legacy `AIza…` also works. Both auth headers are sent.
- **Cerebras**: key valid but returns **402** — free tier not active on the account.
  Real models are `gpt-oss-120b` and `gemma-4-31b`.
- Free pools (OmniRoute `auto/best-free`, opencode, llm7) answer small probes but
  fail on real research payloads. Do not rely on them.
- Keys live in `var/secrets.json` (0600, gitignored). `ios key set <provider>` reads the
  clipboard; keys are never CLI arguments, never printed, and `redact()` scrubs them from
  provider errors.

## Hard-won environment facts
- OmniRoute refuses Node < 22.22.2. Fixed by nvm Node **v22.23.2** + PATH in
  `~/Library/LaunchAgents/io.omniroute.server.plist`. **`launchctl kickstart -k` reuses the
  cached job definition — a plist edit needs `bootout` + `bootstrap`.**
- OmniRoute streams SSE unless `"stream": false`. Reasoning models can return empty
  `content` with everything in `reasoning_content` (treated as failure → failover).
- Trackr API: response shape changed to `{"programmes": [...]}`; it **throttles with
  HTTP 200 + an empty array**, never 429 — `looks_throttled()` refuses to treat that as
  truth. Bun's `process.exit()` truncated large stdout at 64KB (now `process.exitCode`).
- SQLite has one writer: the orchestrator commits per question; LLM cache writes are
  best-effort.

## Open — needs the user
1. Rotate the Gemini and Cerebras keys (both were pasted into a chat transcript).
2. `ios trackr login` → sign in → `ios trackr capture` → change one status.
3. Profile: the only literal `[TODO]` is **referees** (line 106). Blocks cover letters,
   not the CV.
4. **Skill names are dirty**: the profile importer splits `Skill: a, b (x, y)` on every
   comma, so ~5 names are fragments (`FX)`, `macro analysis (inflation`). `is_clean_skill`
   withholds them from the CV; the real fix is in the phase-2 importer.

## Open — next build steps
- **Phase 6 browser assist**: Greenhouse + tal.net + generic only. ~35% of listings
  end-to-end. Workday (19%) stays manual — it requires account creation, which is refused.
- **Phase 7**: outcomes and the learning loop.

## Caution
The Atlas Holdings (id 22) research flagged that its London posting may be an
auto-generated aggregator placeholder — Atlas is a US firm in Greenwich, CT. Verify before
spending effort, despite the 14 Sep deadline.
