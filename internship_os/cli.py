"""The daily interface.

`doctor` exists because every external dependency here fails in a different, confusing way:
pdflatex is off PATH, bun runs the trackr CLI, the LLM gateway may be down. One command
that says which, in plain words, beats discovering it mid-application.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .config import settings

app = typer.Typer(
    add_completion=False,
    help="UK internship application intelligence OS.",
    no_args_is_help=True,
)
console = Console()

OK, BAD, WARN = "[green]ok[/green]", "[red]FAIL[/red]", "[yellow]warn[/yellow]"


@app.command()
def init() -> None:
    """Create the database and bring it to the latest migration."""
    settings.ensure_dirs()
    res = subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        cwd=Path(__file__).resolve().parent.parent,
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        console.print(f"[red]migration failed[/red]\n{res.stderr}")
        raise typer.Exit(1)
    console.print(f"{OK} database ready at [dim]{settings.db_url}[/dim]")


@app.command()
def doctor() -> None:
    """Check every external dependency and report what is broken, in plain words."""
    settings.ensure_dirs()
    table = Table(show_header=True, header_style="bold")
    table.add_column("check")
    table.add_column("status")
    table.add_column("detail", overflow="fold")
    failures = 0

    # database
    try:
        asyncio.run(_check_db())
        table.add_row("database", OK, str(settings.db_url).replace("sqlite+aiosqlite:///", ""))
    except Exception as exc:  # noqa: BLE001
        failures += 1
        table.add_row("database", BAD, f"{exc} — run `internship-os init`")

    # pdflatex
    if settings.pdflatex_path.exists():
        table.add_row("pdflatex", OK, str(settings.pdflatex_path))
    else:
        failures += 1
        table.add_row(
            "pdflatex", BAD, f"not at {settings.pdflatex_path} — CV compiles will fail"
        )

    # poppler (pdftotext) — optional, degrades ATS checks only
    if shutil.which("pdftotext"):
        table.add_row("pdftotext", OK, shutil.which("pdftotext") or "")
    else:
        table.add_row("pdftotext", WARN, "missing — ATS text-layer checks will be skipped")

    # bun + trackr CLI
    if shutil.which(settings.bun_path):
        table.add_row("bun", OK, shutil.which(settings.bun_path) or "")
    else:
        failures += 1
        table.add_row("bun", BAD, "not on PATH — trackr ingestion will fail")
    if settings.trackr_cli_path.exists():
        table.add_row("trackr cli", OK, str(settings.trackr_cli_path))
    else:
        failures += 1
        table.add_row("trackr cli", BAD, f"not at {settings.trackr_cli_path}")

    # candidate profile sources
    for label, path in (
        ("profile md", settings.profile_md_path),
        ("master cv", settings.master_cv_path),
    ):
        if path.exists():
            table.add_row(label, OK, str(path))
        else:
            failures += 1
            table.add_row(label, BAD, f"not at {path}")

    # LLM gateway — listed models
    try:
        models = asyncio.run(_check_llm())
        chain = settings.research_model_chain()
        missing = [m for m in chain if m not in models]
        if missing:
            table.add_row(
                "llm gateway",
                WARN,
                f"{len(models)} models; not offered: {', '.join(missing)}",
            )
        else:
            table.add_row(
                "llm gateway", OK, f"{len(models)} models; research chain resolves"
            )
    except Exception as exc:  # noqa: BLE001
        failures += 1
        table.add_row("llm gateway", BAD, f"{settings.llm_base_url} unreachable ({exc})")

    # A gateway that lists models can still serve none of them, so actually ask one.
    try:
        model_used = asyncio.run(_probe_llm())
        table.add_row("llm completion", OK, f"answered by {model_used}")
    except Exception as exc:  # noqa: BLE001
        failures += 1
        table.add_row(
            "llm completion",
            BAD,
            f"no model in the research chain could answer — {exc}",
        )

    console.print(table)
    console.print(
        "\n[dim]Note: automating employer ATS portals may conflict with their terms. "
        "This system runs headed, on your own session, one application at a time, and "
        "never submits without your typed approval. That judgement stays yours.[/dim]"
    )
    if failures:
        console.print(f"\n[red]{failures} check(s) failed.[/red]")
        raise typer.Exit(1)
    console.print("\n[green]All checks passed.[/green]")


key_app = typer.Typer(help="Provider keys, stored locally and never printed.", no_args_is_help=True)
app.add_typer(key_app, name="key")


@key_app.command("set")
def key_set(
    provider: str = typer.Argument(..., help="gemini | cerebras | groq | openrouter"),
    from_clipboard: bool = typer.Option(
        True, "--from-clipboard/--prompt", help="Read the key from the clipboard"
    ),
) -> None:
    """Store a provider key.

    The key is never accepted as a command-line argument (argv is visible to `ps` and
    lands in shell history), never echoed, and never logged. Copy it, run this, done.
    """
    from .backends import DIRECT
    from .secrets import clear_clipboard, masked, read_clipboard, set_secret

    if provider not in DIRECT:
        console.print(f"[red]Unknown provider '{provider}'.[/red] Known: {', '.join(DIRECT)}")
        raise typer.Exit(1)

    if from_clipboard:
        value = read_clipboard()
        if not value:
            console.print("[yellow]Clipboard is empty.[/yellow] Copy the key, then re-run.")
            raise typer.Exit(1)
        if " " in value or "\n" in value.strip():
            console.print(
                "[yellow]The clipboard does not look like an API key[/yellow] "
                "(it contains spaces or newlines). Nothing was stored."
            )
            raise typer.Exit(1)
    else:
        import getpass

        value = getpass.getpass(f"{provider} key (input hidden): ")

    set_secret(provider, value)
    clear_clipboard()
    console.print(
        f"{OK} stored [bold]{provider}[/bold] key {masked(value)} "
        f"[dim]-> {settings.var_dir / 'secrets.json'} (mode 0600)[/dim]"
    )
    console.print("[dim]Clipboard cleared. The key was never printed or sent anywhere.[/dim]")


@key_app.command("list")
def key_list() -> None:
    """Show which keys are stored — masked, never in full."""
    from .backends import DIRECT
    from .secrets import get_secret, masked

    table = Table(show_header=True, header_style="bold")
    table.add_column("provider")
    table.add_column("key")
    table.add_column("status")
    for name in DIRECT:
        value = get_secret(name)
        table.add_row(name, masked(value), OK if value else "[dim]not set[/dim]")
    console.print(table)


@key_app.command("rm")
def key_rm(provider: str) -> None:
    """Delete a stored key."""
    from .secrets import delete_secret

    if delete_secret(provider):
        console.print(f"{OK} removed [bold]{provider}[/bold]")
    else:
        console.print(f"[yellow]No stored key for {provider}[/yellow]")


profile_app = typer.Typer(help="Your candidate profile and evidence.", no_args_is_help=True)
app.add_typer(profile_app, name="profile")

research_app = typer.Typer(help="Evidence-backed company research.", no_args_is_help=True)
app.add_typer(research_app, name="research")


@research_app.command("run")
def research_run(
    opportunity_id: int,
    depth: str = typer.Option("standard", help="quick | standard | deep | extreme"),
) -> None:
    """Research one opportunity. Every claim ends up traceable to a quoted source."""
    report = asyncio.run(_research_run(opportunity_id, depth))
    console.print(
        f"{OK} {report.claims} claims from {report.excerpts} excerpts · "
        f"{report.pages_fetched} pages · {report.recalled} questions answered from memory"
    )
    if report.discarded_quotes:
        console.print(
            f"  [yellow]{report.discarded_quotes} quote(s) discarded[/yellow] "
            "[dim]— not found verbatim in the fetched page[/dim]"
        )
    if report.open_questions:
        console.print("  [dim]still unanswered:[/dim]")
        for q in report.open_questions[:5]:
            console.print(f"    · {q}")


async def _research_run(opportunity_id: int, depth: str):
    from .db import session_scope
    from .models.company import Company
    from .models.opportunity import Opportunity
    from .research.orchestrator import run_research
    from .research.sources.fetch import domain_of

    async with session_scope() as s:
        opp = await s.get(Opportunity, opportunity_id)
        if opp is None:
            raise typer.BadParameter(f"no opportunity with id {opportunity_id}")
        company = await s.get(Company, opp.company_id)
        domains = {domain_of(u) for u in (company.careers_url, company.website, opp.apply_url) if u}
        console.print(
            f"[dim]Researching {company.name} — {opp.title} ({depth})…[/dim]"
        )
        return await run_research(
            s,
            company=company.name,
            role=opp.title,
            subject_type="company",
            subject_id=company.id,
            depth=depth,
            company_domains={d for d in domains if d},
        )


@research_app.command("show")
def research_show(opportunity_id: int) -> None:
    """What we know about this company, with confidence, sources and what we don't know."""
    data = asyncio.run(_research_show(opportunity_id))
    if not data:
        console.print("[yellow]No research yet — run `research run <id>`.[/yellow]")
        raise typer.Exit(1)
    company_name, grouped, open_qs = data
    console.print(f"\n[bold]{company_name}[/bold] — evidence-backed findings\n")
    for topic, claims in grouped.items():
        console.print(f"[bold]{topic}[/bold]")
        for claim, sources in claims:
            colour = (
                "green" if claim.confidence >= 0.75
                else "yellow" if claim.confidence >= 0.5
                else "red"
            )
            from .research.confidence import band

            console.print(
                f"  [{colour}]{claim.confidence:.2f}[/{colour}] "
                f"[dim]({claim.epistemic_class}, {band(claim.confidence)})[/dim] "
                f"{claim.statement}"
            )
            if claim.bias_flags:
                console.print(f"      [dim]flags: {', '.join(map(str, claim.bias_flags[:4]))}[/dim]")
            for url in sources[:2]:
                console.print(f"      [dim]↳ {url[:88]}[/dim]")
        console.print()
    if open_qs:
        console.print("[bold]Not established[/bold] [dim](the report says what it lacks)[/dim]")
        for q in open_qs[:6]:
            console.print(f"  · {q}")


async def _research_show(opportunity_id: int):
    from collections import defaultdict

    from sqlalchemy import select

    from .db import session_scope
    from .models.company import Company
    from .models.opportunity import Opportunity
    from .models.research import (
        ClaimEvidence,
        ResearchClaim,
        ResearchExcerpt,
        ResearchQuestion,
        ResearchSource,
    )

    async with session_scope() as s:
        opp = await s.get(Opportunity, opportunity_id)
        if opp is None:
            return None
        company = await s.get(Company, opp.company_id)
        claims = (
            (
                await s.execute(
                    select(ResearchClaim)
                    .where(
                        ResearchClaim.subject_type == "company",
                        ResearchClaim.subject_id == company.id,
                        ResearchClaim.superseded_by_id.is_(None),
                    )
                    .order_by(ResearchClaim.topic, ResearchClaim.confidence.desc())
                )
            )
            .scalars()
            .all()
        )
        if not claims:
            return None
        grouped: dict[str, list] = defaultdict(list)
        for claim in claims:
            urls = (
                (
                    await s.execute(
                        select(ResearchSource.url)
                        .join(ResearchExcerpt, ResearchExcerpt.source_id == ResearchSource.id)
                        .join(ClaimEvidence, ClaimEvidence.excerpt_id == ResearchExcerpt.id)
                        .where(ClaimEvidence.claim_id == claim.id)
                    )
                )
                .scalars()
                .all()
            )
            grouped[claim.topic].append((claim, list(dict.fromkeys(urls))))
        open_qs = (
            (
                await s.execute(
                    select(ResearchQuestion.text).where(
                        ResearchQuestion.status == "unanswerable"
                    )
                )
            )
            .scalars()
            .all()
        )
        return company.name, dict(grouped), list(dict.fromkeys(open_qs))


trackr_app = typer.Typer(
    help="Keep your own Trackr account in sync with this pipeline.", no_args_is_help=True
)
app.add_typer(trackr_app, name="trackr")


@trackr_app.command("login")
def trackr_login() -> None:
    """Open Trackr in a browser so you can sign in. Your password is never seen or stored."""
    from .sync.trackr_account import open_login

    console.print(
        "[dim]Opening a browser. Sign in yourself — this system does not read, request "
        "or store credentials. The session is kept in a local browser profile.[/dim]"
    )
    ok_session = asyncio.run(open_login())
    if ok_session:
        console.print(f"{OK} session saved. Next: [bold]internship-os trackr capture[/bold]")
    else:
        console.print(
            "[yellow]No Trackr cookies found — if you did not finish signing in, "
            "run this again.[/yellow]"
        )


@trackr_app.command("capture")
def trackr_capture() -> None:
    """Record the request Trackr sends when you change a status, so it can be replayed."""
    from .sync.trackr_account import capture_status_call

    recipe = asyncio.run(capture_status_call())
    if recipe is None:
        console.print(
            "[yellow]No write request captured.[/yellow] Change one programme's status in "
            "the browser before closing it, and make sure you are signed in."
        )
        raise typer.Exit(1)
    console.print(f"{OK} captured [bold]{recipe.method} {recipe.url}[/bold]")
    if recipe.body:
        console.print(f"  [dim]body: {str(recipe.body)[:200]}[/dim]")

    ids = asyncio.run(_known_programme_ids())
    templated, found = _templatise(recipe, ids)
    path = templated.save()

    if "programme_id" in found:
        console.print(f"  {OK} recognised programme id [bold]{found['programme_id']}[/bold]")
    else:
        console.print(
            "  [yellow]could not find a known programme id in the request[/yellow] — "
            "push will refuse rather than risk updating the wrong row. Re-capture using a "
            "programme this system already tracks."
        )
    if "status" in found:
        console.print(f"  {OK} recognised status label [bold]{found['status']}[/bold]")
    console.print(f"  [dim]template saved to {path}[/dim]")


def _templatise(recipe, ids):
    from .sync.trackr_account import templatise

    return templatise(recipe, ids)


async def _known_programme_ids() -> set[str]:
    from sqlalchemy import select

    from .db import session_scope
    from .models.opportunity import Opportunity

    async with session_scope() as s:
        rows = (await s.execute(select(Opportunity.source_ref))).scalars().all()
    return {r for r in rows if r}


@trackr_app.command("push")
def trackr_push(
    opportunity_id: int = typer.Argument(None, help="One opportunity; omit for all changed"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the request, send nothing"),
) -> None:
    """Push local pipeline status into your Trackr account."""
    try:
        results = asyncio.run(_trackr_push(opportunity_id, dry_run))
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    if not results:
        console.print("[yellow]Nothing to push.[/yellow]")
        return
    for label, outcome in results:
        console.print(f"  {label}: {outcome}")


async def _trackr_push(opportunity_id: int | None, dry_run: bool):
    from sqlalchemy import select

    from .db import session_scope
    from .models.company import Company
    from .models.opportunity import Opportunity
    from .sync.trackr_account import STATUS_MAP, StatusRecipe, push_status, render

    recipe = StatusRecipe.load()
    if recipe is None:
        raise FileNotFoundError(
            "no captured recipe — run `internship-os trackr login` then `trackr capture`"
        )

    async with session_scope() as s:
        stmt = select(Opportunity, Company).join(Company, Opportunity.company_id == Company.id)
        if opportunity_id:
            stmt = stmt.where(Opportunity.id == opportunity_id)
        else:
            stmt = stmt.where(Opportunity.state.not_in(["DISCOVERED", "EXPIRED"]))
        rows = (await s.execute(stmt)).all()

    results = []
    for opp, company, in [(o, c) for o, c in rows]:
        if not opp.source_ref:
            results.append((f"{company.name} — {opp.title[:34]}", "skipped (no Trackr id)"))
            continue
        label_remote = STATUS_MAP.get(opp.state, "Not Applied")
        if dry_run:
            url, body = render(recipe, opp.source_ref, label_remote)
            results.append(
                (f"{company.name} — {opp.title[:34]}", f"would {recipe.method} {url} {body or ''}")
            )
            continue
        status_code, text = await push_status(opp.source_ref, label_remote)
        ok_push = 200 <= status_code < 300
        results.append(
            (
                f"{company.name} — {opp.title[:34]}",
                f"{'✓' if ok_push else '✗'} {label_remote} (HTTP {status_code}) {'' if ok_push else text}",
            )
        )
    return results


@trackr_app.command("status")
def trackr_status() -> None:
    """Show whether the Trackr write-back path is set up."""
    from .sync.trackr_account import STATUS_MAP, profile_dir, StatusRecipe

    has_profile = any(profile_dir().iterdir()) if profile_dir().exists() else False
    recipe = StatusRecipe.load()
    table = Table(show_header=True, header_style="bold")
    table.add_column("step")
    table.add_column("status")
    table.add_column("detail", overflow="fold")
    table.add_row(
        "browser session",
        OK if has_profile else WARN,
        str(profile_dir()) if has_profile else "run `trackr login`",
    )
    table.add_row(
        "status recipe",
        OK if recipe else WARN,
        f"{recipe.method} {recipe.url}" if recipe else "run `trackr capture`",
    )
    console.print(table)
    console.print("\n[bold]state mapping[/bold] [dim](local → Trackr)[/dim]")
    seen: set[str] = set()
    for local, remote in STATUS_MAP.items():
        if remote in seen:
            continue
        seen.add(remote)
        sources = [k for k, v in STATUS_MAP.items() if v == remote]
        console.print(f"  {remote:<12} ← {', '.join(sources[:4])}{' …' if len(sources) > 4 else ''}")


@profile_app.command("import")
def profile_import(
    dry_run: bool = typer.Option(False, "--dry-run", help="Parse and report, write nothing")
) -> None:
    """Load the profile markdown into the database. Idempotent."""
    report, issues = asyncio.run(_profile_import(dry_run))
    verb = "would create" if dry_run else "created"
    console.print(
        f"{OK} {verb} [bold]{report.created_experiences}[/bold] experiences, "
        f"[bold]{report.created_evidence}[/bold] evidence items, "
        f"[bold]{report.created_skills}[/bold] skills "
        f"([dim]{report.unchanged} already present, {report.source_bullets} source bullets[/dim])"
    )
    if issues:
        console.print(
            "\n[yellow]Source disagreements[/yellow] "
            "[dim](reported, never merged — you decide which source is right)[/dim]"
        )
        for issue in issues:
            console.print(f"  · {issue}")


async def _profile_import(dry_run: bool):
    from .db import session_scope
    from .ingest.profile import cross_check_master_cv, import_profile, parse_profile_markdown

    parsed = parse_profile_markdown(settings.profile_md_path)
    issues = cross_check_master_cv(parsed, settings.master_cv_path)
    async with session_scope() as s:
        report = await import_profile(s, parsed, dry_run=dry_run)
    return report, issues


@profile_app.command("show")
def profile_show() -> None:
    """What the database actually holds about you."""
    data = asyncio.run(_profile_show())
    if data is None:
        console.print("[yellow]No profile imported yet — run `profile import`.[/yellow]")
        raise typer.Exit(1)
    candidate, languages, experiences, evidence_n, skills_n = data
    console.print(f"\n[bold]{candidate.full_name}[/bold]")
    console.print(f"[dim]{candidate.email} · {candidate.phone} · {candidate.location_term}[/dim]")
    if candidate.visa_status:
        console.print(f"[dim]work rights: {candidate.visa_status}[/dim]")
    console.print(
        f"[dim]languages: {', '.join(f'{l.language} ({l.level})' for l in languages)}[/dim]\n"
    )
    table = Table(show_header=True, header_style="bold")
    for col in ("kind", "organisation", "title", "period", "evidence"):
        table.add_column(col, overflow="fold")
    for exp, n in experiences:
        table.add_row(
            exp.kind,
            exp.organisation[:34],
            (exp.title or "—")[:34],
            f"{exp.start_date or '—'} → {'present' if exp.is_current else (exp.end_date or '—')}",
            str(n),
        )
    console.print(table)
    console.print(f"[dim]{evidence_n} evidence items · {skills_n} skills[/dim]")


async def _profile_show():
    from sqlalchemy import func, select

    from .db import session_scope
    from .models.candidate import Candidate, CandidateLanguage, EvidenceItem, Experience, Skill

    async with session_scope() as s:
        candidate = (await s.execute(select(Candidate))).scalars().first()
        if candidate is None:
            return None
        languages = (
            (
                await s.execute(
                    select(CandidateLanguage).where(
                        CandidateLanguage.candidate_id == candidate.id
                    )
                )
            )
            .scalars()
            .all()
        )
        exps = (
            (
                await s.execute(
                    select(Experience)
                    .where(Experience.candidate_id == candidate.id)
                    .order_by(Experience.kind, Experience.start_date.desc())
                )
            )
            .scalars()
            .all()
        )
        counts = []
        for e in exps:
            n = await s.scalar(
                select(func.count())
                .select_from(EvidenceItem)
                .where(EvidenceItem.experience_id == e.id)
            )
            counts.append((e, n))
        evidence_n = await s.scalar(select(func.count()).select_from(EvidenceItem))
        skills_n = await s.scalar(select(func.count()).select_from(Skill))
        return candidate, languages, counts, evidence_n, skills_n


@profile_app.command("verify")
def profile_verify() -> None:
    """Prove every stored evidence item still exists in its source file at its recorded line.

    This is the anti-fabrication audit run against the database itself.
    """
    bad, total = asyncio.run(_profile_verify())
    if bad:
        console.print(f"[red]{len(bad)} of {total} evidence items no longer match source:[/red]")
        for item in bad[:15]:
            console.print(f"  · {item}")
        raise typer.Exit(1)
    console.print(f"{OK} all [bold]{total}[/bold] evidence items trace to their source line")


async def _profile_verify():
    from pathlib import Path

    from sqlalchemy import select

    from .db import session_scope
    from .ingest.profile import normalize_for_match
    from .models.candidate import EvidenceItem

    cache: dict[str, list[str]] = {}
    bad: list[str] = []
    async with session_scope() as s:
        items = (await s.execute(select(EvidenceItem))).scalars().all()
        for item in items:
            if item.source_file not in cache:
                path = Path(item.source_file)
                cache[item.source_file] = (
                    path.read_text(encoding="utf-8").splitlines() if path.exists() else []
                )
            lines = cache[item.source_file]
            if not item.source_locator or not item.source_locator.startswith("L"):
                continue
            n = int(item.source_locator[1:])
            line = lines[n - 1] if 0 < n <= len(lines) else ""
            if normalize_for_match(item.text) not in normalize_for_match(line):
                bad.append(f"{item.source_locator}: {item.text[:70]}")
    return bad, len(items)


@profile_app.command("evidence")
def profile_evidence_add(
    text: str,
    organisation: str = typer.Option(..., "--org", help="Experience to attach it to"),
) -> None:
    """Record a fact you confirmed in conversation.

    Without this, a true fact that only ever lived in a chat gets stripped later as a
    fabrication, because the grounding audit checks against the database, not the chat.
    """
    ok_added = asyncio.run(_evidence_add(text, organisation))
    if not ok_added:
        console.print(f"[red]No experience matching '{organisation}'[/red]")
        raise typer.Exit(1)
    console.print(f"{OK} evidence recorded against [bold]{organisation}[/bold]")


async def _evidence_add(text: str, organisation: str) -> bool:
    from sqlalchemy import select

    from .db import session_scope
    from .ingest.metrics import extract_metric
    from .ingest.profile import normalize_for_match
    from .models.candidate import Candidate, EvidenceItem, Experience

    async with session_scope() as s:
        candidate = (await s.execute(select(Candidate))).scalars().first()
        if candidate is None:
            return False
        exp = (
            await s.execute(
                select(Experience).where(
                    Experience.candidate_id == candidate.id,
                    Experience.organisation.ilike(f"%{organisation}%"),
                )
            )
        ).scalars().first()
        if exp is None:
            return False
        metric = extract_metric(text)
        s.add(
            EvidenceItem(
                candidate_id=candidate.id,
                experience_id=exp.id,
                text=text,
                normalized_text=normalize_for_match(text),
                category="metric" if metric else "achievement",
                metric_value=metric.value if metric else None,
                metric_unit=metric.unit if metric else None,
                metric_approx=metric.approx if metric else False,
                verified_by="user_confirmed",
                source_file="conversation",
                source_locator=None,
            )
        )
        return True


sync_app = typer.Typer(help="Pull opportunities from a source.", no_args_is_help=True)
opp_app = typer.Typer(help="Inspect and move opportunities.", no_args_is_help=True)
app.add_typer(sync_app, name="sync")
app.add_typer(opp_app, name="opp")


@sync_app.command("trackr")
def sync_trackr(
    region: str = "UK",
    industry: str = typer.Option("Finance,Tech", help="Comma-separated: Finance,Tech,Law"),
    season: str = "2027",
    type_: str = typer.Option("summer-internships", "--type"),
    open_only: bool = typer.Option(False, "--open", help="Only currently-open programmes"),
) -> None:
    """Ingest from The Trackr via the existing Bun CLI."""
    report = asyncio.run(_sync_trackr(region, industry, season, type_, open_only))
    console.print(
        f"{OK} [bold]{report.created}[/bold] new, "
        f"[bold]{report.updated}[/bold] updated, "
        f"{report.unchanged} unchanged, {report.expired} expired "
        f"({report.total} seen)"
    )
    for change in report.changes[:20]:
        console.print(f"  [yellow]changed[/yellow] {change}")
    for warning in report.warnings:
        console.print(f"  [yellow]warning[/yellow] {warning}")


async def _sync_trackr(region, industry, season, type_, open_only):
    from .db import session_scope
    from .ingest.service import (
        SyncReport,
        expire_past_deadline,
        looks_throttled,
        seed_ats_platforms,
        upsert_draft,
    )
    from .ingest.trackr import TrackrAdapter

    adapter = TrackrAdapter()
    report = SyncReport()
    async with session_scope() as s:
        await seed_ats_platforms(s)
        for ind in [i.strip() for i in industry.split(",") if i.strip()]:
            rows = await adapter.fetch(
                region=region, industry=ind, season=season, type_=type_, open_only=open_only
            )
            if await looks_throttled(s, rows, adapter.key):
                report.warnings.append(
                    f"{ind}: source returned an empty list but we already hold "
                    f"{adapter.key} opportunities — treating as throttled, not as truth. "
                    "Nothing was changed for this scope. Retry in a few minutes."
                )
                continue
            for raw in rows:
                draft = adapter.to_draft(
                    raw, industry=ind, season=season, programme_type=type_, region=region
                )
                await upsert_draft(s, draft, report)
        await expire_past_deadline(s, report)
    return report


@opp_app.command("list")
def opp_list(
    state: str = typer.Option(None, help="Filter by state, e.g. DISCOVERED"),
    closing_within: int = typer.Option(None, "--closing-within", help="Days until deadline"),
    visa: bool = typer.Option(False, "--visa", help="Only visa-sponsoring employers"),
    quant: bool = typer.Option(False, "--quant", help="Only quant/trading firms"),
    track: str = typer.Option(None, help="finance | tech | both"),
    limit: int = 40,
) -> None:
    """List opportunities, soonest deadline first."""
    rows = asyncio.run(_opp_list(state, closing_within, visa, quant, track, limit))
    if not rows:
        console.print("[yellow]No opportunities match those filters.[/yellow]")
        return
    table = Table(show_header=True, header_style="bold")
    for col in ("id", "company", "role", "closes", "visa", "state"):
        table.add_column(col, overflow="fold")
    for opp, company, days in rows:
        closes = (
            f"{opp.closing_date} ({days}d)"
            if opp.closing_date
            else ("rolling" if opp.rolling else "—")
        )
        style = "red" if days is not None and days <= 21 else ""
        table.add_row(
            str(opp.id),
            ("● " if opp.is_quant else "") + company.name,
            opp.title[:52],
            f"[{style}]{closes}[/{style}]" if style else closes,
            {"yes": "✓", "no": "✗"}.get(company.sponsors_visa, "?"),
            opp.state,
        )
    console.print(table)
    console.print(f"[dim]{len(rows)} shown[/dim]")


async def _opp_list(state, closing_within, visa, quant, track, limit):
    from datetime import UTC, date, datetime, timedelta

    from sqlalchemy import select

    from .db import session_scope
    from .models.company import Company
    from .models.opportunity import Opportunity

    stmt = select(Opportunity, Company).join(Company, Opportunity.company_id == Company.id)
    if state:
        stmt = stmt.where(Opportunity.state == state.upper())
    if visa:
        stmt = stmt.where(Company.sponsors_visa == "yes")
    if quant:
        stmt = stmt.where(Opportunity.is_quant.is_(True))
    if track:
        stmt = stmt.where(Opportunity.track.in_([track, "both"]))
    if closing_within:
        cutoff = (datetime.now(UTC).date() + timedelta(days=closing_within)).isoformat()
        stmt = stmt.where(
            Opportunity.closing_date.is_not(None), Opportunity.closing_date <= cutoff
        )

    async with session_scope() as s:
        rows = (await s.execute(stmt)).all()

    todays = date.today()

    def days_left(opp):
        if not opp.closing_date:
            return None
        return (date.fromisoformat(opp.closing_date) - todays).days

    enriched = [(o, c, days_left(o)) for o, c in rows]
    enriched.sort(key=lambda r: (r[2] is None, r[2] if r[2] is not None else 0, r[1].name))
    return enriched[:limit]


@opp_app.command("show")
def opp_show(opportunity_id: int) -> None:
    """Full record, requirements and state history for one opportunity."""
    data = asyncio.run(_opp_show(opportunity_id))
    if data is None:
        console.print(f"[red]No opportunity with id {opportunity_id}[/red]")
        raise typer.Exit(1)
    opp, company, events, ats = data
    console.print(f"\n[bold]{company.name}[/bold] — {opp.title}")
    console.print(
        f"[dim]{opp.industry} · {opp.programme_type} · {opp.season} · "
        f"{', '.join(opp.categories or []) or 'uncategorised'}[/dim]\n"
    )
    table = Table(show_header=False)
    table.add_column("field", style="dim")
    table.add_column("value", overflow="fold")
    table.add_row("state", opp.state)
    table.add_row("opens", opp.opening_date or "—")
    table.add_row("closes", opp.closing_date or ("rolling" if opp.rolling else "—"))
    table.add_row("visa sponsor", company.sponsors_visa)
    table.add_row("process", opp.process_raw or "—")
    table.add_row(
        "requires",
        f"CV: {'yes' if opp.requires_cv else 'no'} · "
        f"cover letter: {opp.requires_cover_letter} · "
        f"written answers: {opp.requires_written_answers}",
    )
    table.add_row("ats", f"{ats.display_name} ({ats.automation_support})" if ats else "unknown")
    table.add_row("apply", opp.apply_url or "—")
    if opp.notes_raw:
        table.add_row("notes", opp.notes_raw)
    console.print(table)
    console.print("\n[bold]history[/bold]")
    for e in events:
        console.print(
            f"  [dim]{e.occurred_at}[/dim] {e.from_state or '—'} → {e.to_state}"
            + (f"  [dim]({e.reason})[/dim]" if e.reason else "")
        )


async def _opp_show(opportunity_id: int):
    from sqlalchemy import select

    from .db import session_scope
    from .models.application import AtsPlatform
    from .models.company import Company
    from .models.opportunity import Opportunity, OpportunityStateEvent

    async with session_scope() as s:
        opp = await s.get(Opportunity, opportunity_id)
        if opp is None:
            return None
        company = await s.get(Company, opp.company_id)
        ats = await s.get(AtsPlatform, opp.ats_platform_id) if opp.ats_platform_id else None
        events = (
            (
                await s.execute(
                    select(OpportunityStateEvent)
                    .where(OpportunityStateEvent.opportunity_id == opportunity_id)
                    .order_by(OpportunityStateEvent.occurred_at)
                )
            )
            .scalars()
            .all()
        )
        return opp, company, events, ats


@opp_app.command("move")
def opp_move(
    opportunity_id: int,
    to_state: str,
    reason: str = typer.Option(None, "--reason"),
) -> None:
    """Move an opportunity through the pipeline. Always audited."""
    try:
        new_state = asyncio.run(_opp_move(opportunity_id, to_state.upper(), reason))
    except Exception as exc:  # noqa: BLE001 - surfaced verbatim, it explains itself
        console.print(f"[red]refused:[/red] {exc}")
        raise typer.Exit(1) from exc
    console.print(f"{OK} moved to [bold]{new_state}[/bold]")


async def _opp_move(opportunity_id: int, to_state: str, reason: str | None) -> str:
    from .db import session_scope
    from .models.opportunity import Opportunity
    from .strategy.state import transition

    async with session_scope() as s:
        opp = await s.get(Opportunity, opportunity_id)
        if opp is None:
            raise ValueError(f"no opportunity with id {opportunity_id}")
        await transition(s, opp, to_state, reason=reason, actor="user")
        return opp.state


async def _check_db() -> None:
    from sqlalchemy import text

    from .db import engine

    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1 FROM alembic_version LIMIT 1"))


async def _check_llm() -> list[str]:
    from .llm.client import list_models

    return await list_models()


async def _probe_llm() -> str:
    from .llm.client import chat

    result = await chat(
        [{"role": "user", "content": "Reply with exactly: ready"}],
        max_tokens=600,
        purpose="doctor:probe",
    )
    return result.model


doc_app = typer.Typer(help="Generate tailored application documents.", no_args_is_help=True)
app.add_typer(doc_app, name="doc")


@doc_app.command("build")
def doc_build(
    opportunity_id: int,
    show_tex: bool = typer.Option(False, "--show-tex", help="Print the LaTeX source too."),
) -> None:
    """Build a one-page CV tailored to this opportunity.

    Bullets are selected from your evidence, never written: what compiles is text that
    already existed in your profile, so nothing here can drift from the source.
    """
    result = asyncio.run(_doc_build(opportunity_id))
    console.print(
        f"{OK} v{result.version} · {result.bullets} bullets · "
        f"{result.page_count} page(s) · {result.pdf_path}"
    )
    for note in result.dropped:
        console.print(f"  [dim]cut for space: {note}[/dim]")
    for warning in result.warnings:
        console.print(f"  [yellow]warning:[/yellow] {warning}")
    if show_tex:
        console.print(result_tex_of(result))


def result_tex_of(result) -> str:
    from .models.document import DocumentVersion

    from .db import session_scope

    async def _get() -> str:
        async with session_scope() as session:
            version = await session.get(DocumentVersion, result.version_id)
            return version.source_text or ""

    return asyncio.run(_get())


async def _doc_build(opportunity_id: int):
    from .db import session_scope
    from .documents.build import build_cv

    async with session_scope() as session:
        return await build_cv(session, opportunity_id)


@doc_app.command("cover")
def doc_cover(opportunity_id: int) -> None:
    """Draft a cover letter, with every paragraph audited for grounding.

    Paragraphs that cite nothing, cite an id that was never offered, or state a number
    absent from their sources come back `ungrounded` — and an ungrounded version cannot
    be approved, so it cannot be submitted.
    """
    result = asyncio.run(_doc_cover(opportunity_id))
    console.print(f"{OK} cover letter v{result.version} · {len(result.paragraphs)} paragraphs")
    console.print()
    for i, para in enumerate(result.paragraphs, 1):
        colour = {"grounded": "green", "reframed": "cyan", "ungrounded": "red"}[para.grounding]
        console.print(f"[{colour}]{para.grounding:>10}[/{colour}] ¶{i}  [dim]{para.note}[/dim]")
        console.print(f"  {para.text}\n")
    if result.approvable:
        console.print(f"{OK} every paragraph is grounded — this version can be approved")
    else:
        console.print(
            f"  [red]{result.ungrounded} ungrounded paragraph(s)[/red] "
            "[dim]— cannot be approved until fixed or removed[/dim]"
        )


async def _doc_cover(opportunity_id: int):
    from .db import session_scope
    from .documents.cover import build_cover_letter

    async with session_scope() as session:
        return await build_cover_letter(session, opportunity_id)


if __name__ == "__main__":  # pragma: no cover
    app()
