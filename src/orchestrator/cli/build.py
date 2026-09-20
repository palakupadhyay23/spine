"""Plan & build, the intake half: ingest, backlog, openspec. The sdlc sub-app is its own module."""

from __future__ import annotations

from typing import Annotated, Any

import typer

from orchestrator.pkg import RepoCodeExtractor

from ._app import PANEL_BUILD, app
from ._common import _merged_store, _print, _repo_arg

openspec_app = typer.Typer(help="Spec-driven development with OpenSpec (openspec.dev).", no_args_is_help=True)


@app.command("ingest", rich_help_panel=PANEL_BUILD)
def ingest(
    source: Annotated[
        str,
        typer.Option(
            "--source",
            help="Source root, e.g. confluence://<page_id>, jira://<issue-or-project> (read), "
            "notion://<page_id>, openspec://<change-id> (spec-driven), or file://./spec.md.",
        ),
    ],
    create: Annotated[
        bool,
        typer.Option("--create/--dry-run", help="Create issues for real (default: dry-run preview)."),
    ] = False,
    rules: Annotated[
        str | None,
        typer.Option("--rules", help="Path to a gap-rules YAML (defaults to built-ins)."),
    ] = None,
    force: Annotated[
        bool,
        typer.Option("--force", help="Create even when gaps gate the intent-approval bookend."),
    ] = False,
    refresh: Annotated[
        bool,
        typer.Option("--refresh", help="Re-extract from the source (default: reuse the cached backlog)."),
    ] = False,
) -> None:
    """Source (Confluence / Notion / local files) → intents → gaps → specs → Jira backlog.

    Dry-run by default: fetches the source tree, derives intents, flags
    gaps, drafts specs, and prints the would-be Jira issues without writing
    anything. Pass --create to write to Jira (refused when gaps gate
    approval unless --force).

    The lowest-friction source is local files — no SaaS account needed:

        orchestrator ingest --source file://./examples/intake/sample-spec.md

    (An LLM key is still required for the intent/spec stages.)
    """
    import asyncio

    asyncio.run(_run_ingest(source, create=create, rules_path=rules, force=force, refresh=refresh))


async def _run_ingest(
    source: str, *, create: bool, rules_path: str | None, force: bool, refresh: bool
) -> None:
    from orchestrator.core.env import load_local_env

    # Bridge .env → os.environ so LiteLLM sees the provider key and the
    # ORCHESTRATOR_INTAKE_MODEL override is visible to the factory.
    load_local_env()
    from orchestrator.core.llm.client import LLMError
    from orchestrator.intake.cache import analyze_cached
    from orchestrator.intake.factory import IntakeNotConfiguredError, build_service_for
    from orchestrator.intake.service import SourceUriError, parse_source_uri, spec_to_issue_request

    try:
        parse_source_uri(source)  # validate the source URI early
        service = build_service_for(source, dry_run=not create, rules_path=rules_path)
    except (SourceUriError, IntakeNotConfiguredError) as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    try:
        plan = await analyze_cached(service, source, refresh=refresh, log=lambda m: typer.echo(m, err=True))
    except LLMError as exc:
        # Deriving a spec needs a model. A provider that will not answer is an expected
        # condition, and the message already names the model and the way out.
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    _print(
        {
            "documents": len(plan.documents),
            "truncated": plan.truncated,
            "intents": [i.model_dump() for i in plan.intents],
            "gaps": [
                {"intent": g.intent_id, "rule": g.rule_id, "severity": g.severity.value, "message": g.message}
                for g in plan.gaps
            ],
            "blocked": plan.blocked,
            "would_create": [
                {"summary": spec_to_issue_request(s).summary, "intent": s.intent_id} for s in plan.specs
            ],
        }
    )

    if not create:
        typer.echo("\nDry-run: no issues created. Re-run with --create to write to Jira.")
        return
    if plan.blocked and not force:
        typer.echo(
            "\nGaps gate the intent-approval bookend; refusing to create. Resolve the gaps or pass --force.",
            err=True,
        )
        raise typer.Exit(code=3)

    issues = await service.create_issues(plan, link_dependencies=True)
    _print({"created": [{"key": i.key, "url": i.url} for i in issues]})


@openspec_app.command("draft")
def openspec_draft(
    source: Annotated[
        str,
        typer.Option("--source", help="Unstructured source to bootstrap FROM, e.g. confluence://<id>."),
    ],
    out: Annotated[
        str,
        typer.Option("--out", help="OpenSpec root to write into (changes/<id>/ is created under it)."),
    ] = "openspec",
    refresh: Annotated[
        bool,
        typer.Option("--refresh", help="Re-extract from the source (default: reuse the cached backlog)."),
    ] = False,
    overwrite: Annotated[
        bool,
        typer.Option("--overwrite", help="Overwrite existing change files (default: never clobber)."),
    ] = False,
    path: Annotated[
        str | None,
        typer.Argument(help="Repo path to ground the draft against (default: ungrounded)."),
    ] = None,
    repos: Annotated[
        str | None,
        typer.Option("--repos", help="A `.spine/repos.yaml` — ground against every declared repo."),
    ] = None,
    dialect: Annotated[
        str | None, typer.Option("--dialect", help="SQL dialect; default: auto-detect.")
    ] = None,
) -> None:
    """Bootstrap OpenSpec change proposals FROM an unstructured source (the write-back).

    Runs the LLM intake once (source → intents → specs), then renders each as a
    structured `openspec/changes/<id>/` proposal (proposal.md + specs delta + tasks).
    A human polishes the draft, then implements deterministically:

        orchestrator openspec draft --source confluence://<id> --out ./openspec
        # …review/edit openspec/changes/<id>/…
        orchestrator sdlc feature --source openspec://<id> --safe

    Pass a repo path (or `--repos`) to ground the draft against the code — the proposal then
    carries what the graph says, fenced off from the model's prose and labelled. **Without
    one the draft is unchanged from before**, and says so on its own face rather than leaving
    a reader to wonder which mode produced it.
    """
    import asyncio

    asyncio.run(
        _run_openspec_draft(
            source, out=out, refresh=refresh, overwrite=overwrite, path=path, repos=repos, dialect=dialect
        )
    )


def _grounding_for(path: str | None, repos: str | None, dialect: str | None) -> Any:
    """Read the repository the draft was pointed at, and classify what came back.

    **This is the composition root, on purpose.** The evidence needs landing sites, which are
    computed in `sdlc`; `intake` may not import `sdlc` at module level, and moving the landing
    machinery down into `pkg` would drag `CoverageIndex`, `excerpt` and `brief` with it. The
    CLI is the one layer allowed to read both, so it reads both and hands the result down —
    which is also why `render_change` takes grounding as an argument rather than fetching it.
    """
    from orchestrator.intake import pkg_evidence

    if not path and not repos:
        return pkg_evidence.ungrounded()
    if repos:
        store, merged, _repo_set = _merged_store(
            repos, command="openspec draft", extractor=RepoCodeExtractor(sql_dialect=dialect)
        )
        untrusted = tuple(merged.untrusted_keys) if not merged.trusted else ()
        return pkg_evidence.from_store(store, where=repos, untrusted=untrusted)
    from orchestrator.pkg import FactStore, load_or_extract
    from orchestrator.pkg.persistence import repo_state

    with _repo_arg(str(path)) as (repo, _):
        batch = load_or_extract(repo, extractor=RepoCodeExtractor(sql_dialect=dialect))
        # The single-repo path gets no standing for free the way a merged graph does, so ask
        # for it. Without this, D18's warning would fire only under `--repos` — and a dirty
        # single checkout is the far commoner way to draft against unreproducible evidence.
        _sha, dirty = repo_state(repo)
        return pkg_evidence.from_store(
            FactStore(batch), where=str(path), untrusted=(str(path),) if dirty else ()
        )


async def _run_openspec_draft(
    source: str,
    *,
    out: str,
    refresh: bool,
    overwrite: bool,
    path: str | None = None,
    repos: str | None = None,
    dialect: str | None = None,
) -> None:
    from pathlib import Path

    from orchestrator.core.env import load_local_env

    load_local_env()
    from orchestrator.core.llm.client import LLMError
    from orchestrator.intake.cache import analyze_cached
    from orchestrator.intake.factory import IntakeNotConfiguredError, build_service_for
    from orchestrator.intake.openspec_writer import change_id_for, render_change, write_change
    from orchestrator.intake.service import SourceUriError, parse_source_uri

    try:
        parse_source_uri(source)  # validate early
        service = build_service_for(source, dry_run=True, rules_path=None)
    except (SourceUriError, IntakeNotConfiguredError) as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    try:
        plan = await analyze_cached(service, source, refresh=refresh, log=lambda m: typer.echo(m, err=True))
    except LLMError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    root = Path(out)
    # Once, before the loop. One source drafts N changes, and extraction is the expensive half
    # — per-spec it would be paid N times for an answer that cannot differ (D9).
    grounding = _grounding_for(path, repos, dialect)
    intents_by_id = {i.id: i for i in plan.intents}
    drafted: list[dict[str, object]] = []
    for spec in plan.specs:
        intent = intents_by_id.get(spec.intent_id)
        if intent is None:
            continue
        written = write_change(root, intent, render_change(spec, intent, grounding), overwrite=overwrite)
        drafted.append(
            {
                "change_id": change_id_for(intent),
                "source": f"openspec://{change_id_for(intent)}",
                "files": [str(p) for p in written],
                "skipped_existing": not written,
            }
        )
    _print({"root": str(root), "drafted": drafted})
    typer.echo(
        f"\nDrafted {sum(1 for d in drafted if d['files'])} OpenSpec change(s) under {root}/changes/. "
        "Review + polish them, then: orchestrator sdlc feature --source openspec://<change-id> --safe",
        err=True,
    )


@app.command("backlog", rich_help_panel=PANEL_BUILD)
def backlog(
    source: Annotated[
        str,
        typer.Option("--source", help="Source URI whose cached backlog to render, e.g. confluence://<id>."),
    ],
    out: Annotated[
        str | None,
        typer.Option("--out", help="Write the markdown here (default: print to stdout)."),
    ] = None,
) -> None:
    """Render the cached backlog + completion progress as markdown (read-only).

    Reads the persisted backlog (from a prior ingest / sdlc feature run) and
    prints a checkbox ledger: [ ] todo, [~] in progress, [x] done. Pass --out to
    write a BACKLOG.md.
    """
    from orchestrator.intake.backlog_doc import render_markdown, write_backlog
    from orchestrator.intake.cache import load_cached_plan, load_progress

    plan = load_cached_plan(source)
    if plan is None:
        typer.echo(
            f"No cached backlog for {source}. Run `ingest` or `sdlc feature` (optionally --refresh) first.",
            err=True,
        )
        raise typer.Exit(code=1)
    progress = load_progress(source)
    if out:
        typer.echo(f"wrote {write_backlog(out, source, plan, progress)}")
    else:
        typer.echo(render_markdown(source, plan, progress), nl=False)
