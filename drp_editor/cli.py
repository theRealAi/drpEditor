"""Typer-based command line interface.

Run ``drp --help`` for the full command list. Diagnostics go through
``logging`` (enable with ``-v`` / ``-vv``); command *output* is rendered
with rich.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, TypeVar

import typer
from rich.console import Console
from rich.table import Table

from . import __version__, open_project
from .diff import diff_projects, format_project_diff
from .exceptions import DRPError
from .fields_blob import default_registry
from .models import Clip, Project
from .repairs import available_repairs, get_repair
from .utils import hex_dump, setup_logging
from .validation import Validator

app = typer.Typer(
    name="drp",
    help="Inspect, modify, validate, diff, and repair DaVinci Resolve .drp files.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)
console = Console()
logger = logging.getLogger(__name__)

ProjectArg = Annotated[Path, typer.Argument(exists=True, readable=True, help="Input .drp file")]
OutputArg = Annotated[Path, typer.Argument(help="Output .drp file")]


@app.callback()
def main(
    verbose: Annotated[
        int, typer.Option("--verbose", "-v", count=True, help="-v for INFO, -vv for DEBUG")
    ] = 0,
    signatures: Annotated[
        Path | None,
        typer.Option("--signatures", help="JSON signature database of known blob fields"),
    ] = None,
    version: Annotated[bool, typer.Option("--version", help="Print version and exit")] = False,
) -> None:
    """Global options applied before any command."""
    setup_logging(verbose)
    if version:
        console.print(f"drp-editor {__version__}")
        raise typer.Exit()
    if signatures is not None:
        _run(lambda: default_registry.load_json(signatures))


T = TypeVar("T")


def _run(func: Callable[[], T]) -> T:
    """Execute *func*, converting DRPError into a clean CLI failure."""
    try:
        return func()
    except DRPError as exc:
        logger.debug("command failed", exc_info=True)
        console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(code=2) from exc


def _open(path: Path) -> Project:
    return _run(lambda: open_project(path))


@app.command()
def info(project_file: ProjectArg) -> None:
    """Show a summary of the project."""
    project = _open(project_file)
    clips = project.all_clips()
    console.print(f"[bold]{project.name}[/bold]")
    console.print(f"  Archive members: {len(project.archive.namelist())}")
    console.print(f"  Timelines:       {len(project.timelines)}")
    console.print(f"  Clips:           {len(clips)}")
    console.print(f"  Media items:     {len(project.media_pool)}")
    with_blobs = sum(1 for c in clips if c.blob_holder is not None)
    console.print(f"  Clips w/ blobs:  {with_blobs}")


@app.command()
def timelines(project_file: ProjectArg) -> None:
    """List all timelines."""
    project = _open(project_file)
    table = Table("UUID", "Name", "Clips")
    for timeline in project.timelines:
        table.add_row(timeline.uuid, timeline.name, str(len(timeline.clips)))
    console.print(table)


@app.command()
def clips(
    project_file: ProjectArg,
    timeline: Annotated[
        str | None, typer.Option(help="Limit to one timeline (name or UUID)")
    ] = None,
) -> None:
    """List clips, optionally for a single timeline."""
    project = _open(project_file)
    if timeline is not None:
        found = project.find_timeline(uuid=timeline) or project.find_timeline(name=timeline)
        if found is None:
            console.print(f"[red]error:[/red] timeline {timeline!r} not found")
            raise typer.Exit(code=2)
        clip_list = found.clips
    else:
        clip_list = project.all_clips()
    table = Table("UUID", "Name", "Source", "Timeline", "Blob")
    for clip in clip_list:
        table.add_row(
            clip.uuid,
            clip.name,
            clip.source,
            clip.timeline_uuid,
            "yes" if clip.blob_holder else "-",
        )
    console.print(table)


@app.command()
def media(project_file: ProjectArg) -> None:
    """List media pool items."""
    project = _open(project_file)
    table = Table("UUID", "Name", "File path")
    for item in project.media_pool:
        table.add_row(item.uuid, item.name, item.file_path)
    console.print(table)


@app.command()
def search(project_file: ProjectArg, pattern: Annotated[str, typer.Argument()]) -> None:
    """Regex search across names, UUIDs, and file paths."""
    project = _open(project_file)
    hits = _run(lambda: project.search(pattern))
    table = Table("Type", "UUID", "Property", "Value")
    for hit in hits:
        table.add_row(hit.object_type, hit.object_id, hit.property, hit.value)
    console.print(table)
    if not hits:
        console.print("[dim]no matches[/dim]")


@app.command()
def validate(
    project_file: ProjectArg,
    check_files: Annotated[
        bool, typer.Option("--check-files", help="Also verify media paths exist locally")
    ] = False,
) -> None:
    """Validate the project; exit code 1 if errors were found."""
    project = _open(project_file)
    issues = Validator(project, check_files=check_files).run()
    if not issues:
        console.print("[green]OK[/green] no issues found")
        return
    for issue in issues:
        color = "red" if issue.severity == "error" else "yellow"
        suffix = f" [{issue.object_id}]" if issue.object_id else ""
        console.print(
            f"{issue.severity} {issue.code}: {issue.message}{suffix}", style=color, markup=False
        )
    if any(i.severity == "error" for i in issues):
        raise typer.Exit(code=1)


@app.command()
def diff(before: ProjectArg, after: ProjectArg) -> None:
    """Compare two .drp files (great for reverse engineering)."""
    old = _open(before)
    new = _open(after)
    result = _run(lambda: diff_projects(old, new))
    console.print(format_project_diff(result), markup=False)


@app.command()
def patch(
    project_file: ProjectArg,
    output: OutputArg,
    set_values: Annotated[
        list[str],
        typer.Option(
            "--set",
            help=(
                "Change: '<clip-uuid-or-name>.<property>=<value>'. Property may be "
                "'name', 'source', or 'fields_blob.<field>' (integer value)."
            ),
        ),
    ] = [],  # noqa: B006 - typer requires a literal default
    patch_log_out: Annotated[
        Path | None, typer.Option("--log", help="Write applied patches to this JSON file")
    ] = None,
) -> None:
    """Apply property/blob changes and save a new .drp."""

    def run() -> None:
        project = open_project(project_file)
        for spec in set_values:
            target, _, assignment = spec.partition(".")
            prop, sep, value = assignment.partition("=")
            if not sep:
                raise DRPError(f"malformed --set {spec!r}; expected target.property=value")
            clip = project.find_clip(uuid=target) or project.find_clip(name=target)
            if clip is None:
                raise DRPError(f"clip {target!r} not found")
            if prop.startswith("fields_blob."):
                field_name = prop.removeprefix("fields_blob.")
                project.set_blob_field(clip, field_name, int(value, 0))
            else:
                project.set_property(clip, prop, value)
        project.save(output)
        if patch_log_out is not None:
            project.patch_log.save(patch_log_out)
        console.print(f"applied {len(project.patch_log)} patch(es) -> {output}")

    _run(run)


@app.command("dump-fields")
def dump_fields(
    project_file: ProjectArg,
    clip: Annotated[
        str | None, typer.Option(help="Clip name or UUID (default: all clips with blobs)")
    ] = None,
) -> None:
    """Hex-dump FieldsBlobs with any known fields decoded."""
    project = _open(project_file)
    if clip is not None:
        found = project.find_clip(uuid=clip) or project.find_clip(name=clip)
        targets: list[Clip] = [found] if found else []
        if not targets:
            console.print(f"[red]error:[/red] clip {clip!r} not found")
            raise typer.Exit(code=2)
    else:
        targets = [c for c in project.all_clips() if c.blob_holder is not None]
    for target in targets:
        blob = target.fields_blob
        if blob is None:
            continue
        console.print(f"[bold]{target.name or target.uuid}[/bold] ({len(blob)} bytes)")
        known = blob.decode()
        for name, value in known.items():
            spec = blob.known_fields()[name]
            shown = value.hex() if isinstance(value, bytes) else value
            console.print(f"  [{name}] offset 0x{spec.offset:04x}: {shown}", markup=False)
        console.print(hex_dump(blob.raw_bytes))
        console.print()


@app.command("export-json")
def export_json(
    project_file: ProjectArg,
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
    include_fields: Annotated[
        bool, typer.Option("--fields", help="Include FieldsBlob hex and decoded fields")
    ] = False,
) -> None:
    """Export the project model as JSON (for debugging / analysis)."""
    project = _open(project_file)
    text = json.dumps(project.to_dict(include_fields=include_fields), indent=2)
    if output is None:
        console.print_json(text)
    else:
        output.write_text(text, encoding="utf-8")
        console.print(f"wrote {output}")


@app.command("import-json")
def import_json(
    project_file: ProjectArg,
    json_file: Annotated[Path, typer.Argument(exists=True, help="JSON from export-json")],
    output: OutputArg,
) -> None:
    """Apply names/sources from a JSON export back onto a project.

    Objects are matched by UUID; only 'name' and 'source' fields are
    applied. Unknown UUIDs are skipped with a warning.
    """

    def run() -> None:
        project = open_project(project_file)
        data = json.loads(json_file.read_text(encoding="utf-8"))
        applied = 0
        clip_records = list(data.get("unattached_clips", []))
        for tl in data.get("timelines", []):
            clip_records.extend(tl.get("clips", []))
        for record in clip_records:
            clip = project.find_clip(uuid=record.get("uuid", ""))
            if clip is None:
                logger.warning("clip %s not found; skipped", record.get("uuid"))
                continue
            for prop in ("name", "source"):
                value = record.get(prop)
                if value is not None and value != getattr(clip, prop):
                    project.set_property(clip, prop, value)
                    applied += 1
        project.save(output)
        console.print(f"applied {applied} change(s) -> {output}")

    _run(run)


@app.command()
def repair(
    name: Annotated[str, typer.Argument(help="Repair plugin name (see --list)")] = "",
    project_file: Annotated[Path | None, typer.Argument(exists=True)] = None,
    output: Annotated[Path | None, typer.Argument()] = None,
    list_plugins: Annotated[bool, typer.Option("--list", help="List available repairs")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Scan only, change nothing")] = False,
) -> None:
    """Run a repair plugin: scan, fix, validate, save."""
    if list_plugins or not name:
        for plugin_name, cls in sorted(available_repairs().items()):
            console.print(f"[bold]{plugin_name}[/bold]  {cls.description}")
        return
    if project_file is None or (output is None and not dry_run):
        console.print("[red]error:[/red] usage: drp repair <name> <input.drp> <output.drp>")
        raise typer.Exit(code=2)

    def run() -> None:
        plugin = get_repair(name)
        project = open_project(project_file)
        findings = plugin.scan(project)
        for finding in findings:
            console.print(f"found: {finding.description}", markup=False)
        if not findings:
            console.print("[green]nothing to repair[/green]")
        if dry_run:
            return
        patches = plugin.repair(project)
        issues = plugin.validate(project)
        for issue in issues:
            console.print(f"[red]{issue.code}[/red]: {issue.message}")
        if issues:
            raise DRPError("post-repair validation failed; output not written")
        assert output is not None
        project.save(output)
        console.print(f"applied {len(patches)} patch(es) -> {output}")

    _run(run)


@app.command("repair-ai-upscale")
def repair_ai_upscale(project_file: ProjectArg, output: OutputArg) -> None:
    """Disable AI Super Scale on all clips and save a fixed project."""
    repair(name="ai-upscale", project_file=project_file, output=output)


@app.command()
def ui(
    project_file: Annotated[
        Path | None, typer.Argument(exists=True, help="Optionally preload this .drp")
    ] = None,
    host: Annotated[str, typer.Option(help="Bind address")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port to listen on")] = 8765,
    open_browser: Annotated[
        bool, typer.Option("--open/--no-open", help="Open the UI in a browser")
    ] = True,
) -> None:
    """Launch the local web UI for browsing and repairing projects."""
    import webbrowser

    import uvicorn

    from .web.server import create_app
    from .web.session import Session

    session = Session()
    if project_file is not None:
        _run(lambda: session.open(project_file))
        console.print(f"preloaded {project_file}")
    url = f"http://{host}:{port}"
    console.print(f"drp editor UI running at [bold]{url}[/bold] (Ctrl+C to stop)")
    if open_browser:
        webbrowser.open(url)
    uvicorn.run(create_app(session), host=host, port=port, log_level="warning")


if __name__ == "__main__":
    app()
