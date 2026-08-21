# internship-os

UK internship application intelligence, for one person. Turns a listing into research →
strategy → tailored documents → a human-approved submission → a recorded outcome that
feeds the next application.

The advantage is meant to come from the **research and evidence layer**, not from
automating form-filling. Form-filling is the last mile, and the smaller half of the value.

## Two rules, enforced as structure rather than good intentions

1. **Nothing reaches a document without evidence.** `evidence_items` is the whitelist of
   quotable facts about you, each tied to a source file and locator. Every generated span is
   tagged `grounded | reframed | ungrounded`, and a document version **cannot be approved**
   while any span is ungrounded. Rephrasing an evidence item for a posting's keywords is
   allowed; inventing one is structurally impossible.
2. **Nothing submits without a scoped approval.** Submission requires an approval token
   bound to a hash of the exact payload — every field value, document id and file hash. The
   hash is recomputed against the live page immediately before submitting, so an approval
   cannot be reused for different content. Tokens are single-use and expire.

## Quick start

```bash
uv sync --extra dev
uv run internship-os init      # create the DB, run migrations
uv run internship-os doctor    # check every external dependency
```

## Daily use

```bash
# pull both tracks from The Trackr
uv run internship-os sync trackr --industry Finance,Tech --season 2027

# what is worth looking at
uv run internship-os opp list --visa --closing-within 45
uv run internship-os opp list --quant
uv run internship-os opp show 22

# move something through the pipeline (always audited, guards enforced)
uv run internship-os opp move 22 QUALIFIED --reason "gates passed"
```

## Where things live

| Path | What |
|---|---|
| `internship_os/models/` | Schema, including the two invariants in `invariants.py` |
| `internship_os/ingest/` | Source adapters. `trackr.py` wraps the existing Bun CLI |
| `internship_os/strategy/state.py` | The state machine — the only writer of `Opportunity.state` |
| `internship_os/llm/` | Provider-agnostic client (OmniRoute by default) + prompt library |
| `docs/constraints.md` | What this refuses to do, and where it hits real walls |

## Models

Research is outsourced to free models (`auto/best-free` plus an `oc/*-free` failover chain).
Strategy and drafting use `auto/best-reasoning` — and that chain **also ends in free models**,
so if a paid provider runs out of quota the work continues on a weaker model rather than
stopping. Configure via `LLM_RESEARCH_MODELS` / `LLM_REASONING_MODELS`.

`LLM_OFFLINE=1` forbids network calls entirely and serves only cached responses. The test
suite runs fully offline, which is why it costs nothing to run.

## Status

- **Phase 1 — foundation:** done. Config, DB, 31 tables, migrations, LLM client, `doctor`.
- **Phase 3 — ingestion:** done. Trackr adapter, dedup, change detection, ATS classification,
  state machine, CLI.
- **Phase 2 — profile + evidence extraction:** next.
- **Phase 3.5 — Trackr account write-back:** next. Requires discovering the status endpoint
  at runtime from a session you log into yourself.
- Phases 4–7 (research engine, documents, browser assist, learning loop) follow.
