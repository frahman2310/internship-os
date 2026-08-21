# Constraints, refusals and honest limits

Written down because the failure mode of a system like this is quiet overreach: it looks
confident, so you believe it. Everything below is a design position, not a TODO.

## What the system refuses to do

These are enforced behaviours, not policy prose:

- **Submit without a scope-matched, unconsumed, unexpired approval token.** The state machine
  cannot reach `APPLYING` without one, and the payload hash is recomputed against the live
  page before submitting.
- **Assert a fact about you that is not traceable to an `evidence_items` row.** A document
  version with an ungrounded span cannot be approved.
- **Create accounts, enter passwords, solve CAPTCHAs, or handle MFA codes.** This removes
  Workday entirely (see below). Not "not yet implemented" — refused.
- **Auto-fill diversity monitoring, date of birth, National Insurance number, passport
  number, or salary expectation.** These are `RESERVED_FOR_HUMAN`. They are yours to give or
  decline, not a system's to infer.
- **Scrape LinkedIn**, or any site whose terms prohibit it.
- **Present an inference or speculation in the same register as a fact.** Claims carry an
  epistemic class and a computed confidence; speculation is capped at 0.35.
- **Report an experiment result below `min_n`.**
- **Silently drop a gate failure.** A posting requiring citizenship is reported with its
  verbatim wording and routed to `REJECTED_BY_GATE`, never quietly filtered away.

## Where it hits real walls

**ATS terms of service.** Greenhouse, Workday and tal.net terms generally prohibit automated
access. The mitigations here — headed browser, your own session, human-typed approval per
submission, one application at a time, no volume — are materially different from a bulk bot,
but they are not compliance in any certified sense. An employer may still treat it as
automation. `doctor` prints this every run; the judgement stays yours.

**Workday is 19% of the real listing set and is `manual_only`**, because it requires creating
an account with a password before the form is reachable. The deliverable there is a prepared
answers file plus the PDFs; you fill the wizard yourself.

**Realistic browser-assist coverage is roughly 35%** of listings end-to-end (Greenhouse +
tal.net + Lever + Workable). The other 65% is reduced to copy-paste from a prepared file. This
is not an apply-to-everything machine, and describing it as one would be a lie.

**LinkedIn is manual-import only.** No scraping, no automated connections, no messaging. This
costs the single richest source of alumni and team data, and there is no compliant
workaround. The gap is accepted, not engineered around.

**Small-n makes statistical learning mostly decorative.** Thirty to sixty applications with a
low single-digit offer rate produces one to three positive outcomes. No experiment on CV
phrasing reaches significance. The `experiments` table defaults to `inconclusive` below
`min_n` rather than pattern-matching noise. The learning that actually pays is qualitative:
verbatim feedback and which stage you fail at.

**Survivorship bias dominates internship research.** Every "how I got into Jane Street" post
was written by someone who got in. The anti-bias controls cap confidence on such claims at
0.4 when no rejections were observed; they cannot manufacture the missing data.

## The Trackr dependency

One unofficial, undocumented, personal-use API, and the system's only automated discovery
source. Two things observed directly:

1. **The response shape changed during development** — from a bare JSON array to
   `{"programmes": [...]}`. The CLI now accepts both.
2. **It throttles by returning HTTP 200 with an empty array**, not a 429. That is
   indistinguishable from "this season has no programmes" unless you already know better.

The second one is the dangerous one, and it is guarded: if a source returns nothing while we
already hold opportunities from it, ingestion treats the response as a **failed fetch, not as
truth**, changes nothing, and says so. Any future logic that closes or deletes "listings that
disappeared" must respect the same guard, or one throttled sync will wipe the pipeline.

The hedges are `raw_payload` on every opportunity (the original record, whatever the schema
does next) and the `SourceAdapter` protocol. The obligation is low query volume.
