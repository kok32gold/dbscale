"""``dbscale`` command-line interface.

dbscale init      scaffold dbscale.yaml + queries/
dbscale inspect   show what DBScale sees in the source database
dbscale run       run the experiment
dbscale report    re-render a saved results file
dbscale explain   run the AI advisor over a saved results file
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.status import Status

from dbscale import __version__
from dbscale.core.config import DEFAULT_CONFIG_TEMPLATE, ConfigError, ExperimentConfig, load_config
from dbscale.core.findings import Severity

app = typer.Typer(
    name="dbscale",
    help="Know how your database will behave before it gets big.",
    add_completion=False,
    no_args_is_help=True,
    rich_markup_mode="rich",
)
console = Console()
err_console = Console(stderr=True)

CONFIG_OPTION = typer.Option("dbscale.yaml", "--config", "-c", help="Path to the experiment configuration")


class ConsoleProgress:
    """Prints ✓ lines as steps complete, with a spinner while they run."""

    def __init__(self, console: Console):
        self.console = console
        self._status: Status | None = None
        self._current = ""

    def start(self, message: str) -> None:
        self._current = message
        self._status = self.console.status(f"{message}…", spinner="dots")
        self._status.start()

    def progress(self, message: str) -> None:
        if self._status is not None:
            self._status.update(f"{self._current}… [dim]{message}[/dim]")

    def _stop(self) -> None:
        if self._status is not None:
            self._status.stop()
            self._status = None

    def done(self, message: str) -> None:
        self._stop()
        self.console.print(f"[green]✓[/green] {message}")

    def fail(self, message: str) -> None:
        self._stop()
        self.console.print(f"[red]✗[/red] {message}")

    def note(self, message: str) -> None:
        self.console.print(f"  [dim]{message}[/dim]")


def _version(value: bool) -> None:
    if value:
        console.print(f"dbscale {__version__}")
        raise typer.Exit()


@app.callback()
def _main(
    version: bool = typer.Option(False, "--version", callback=_version, is_eager=True, help="Show version"),
) -> None:
    return None


def _load(config_path: str) -> ExperimentConfig:
    try:
        return load_config(config_path)
    except ConfigError as exc:
        err_console.print(f"[red]Configuration error:[/red] {exc}")
        raise typer.Exit(code=2) from None


@app.command()
def init(
    directory: Path = typer.Argument(Path("."), help="Where to create the configuration"),
    name: str = typer.Option("my-experiment", "--name", "-n", help="Experiment name"),
    force: bool = typer.Option(False, "--force", help="Overwrite existing files"),
) -> None:
    """Create a dbscale.yaml and an example query to get started."""
    directory.mkdir(parents=True, exist_ok=True)
    config_path = directory / "dbscale.yaml"
    queries_dir = directory / "queries"
    example = queries_dir / "example.sql"
    if config_path.exists() and not force:
        err_console.print(f"[yellow]{config_path} already exists[/yellow] (use --force to overwrite)")
        raise typer.Exit(code=1)
    queries_dir.mkdir(exist_ok=True)
    config_path.write_text(DEFAULT_CONFIG_TEMPLATE.format(name=name))
    if not example.exists() or force:
        example.write_text(
            "-- Replace with a real query from your application.\n"
            "-- Tip: synthetic primary keys are sequential integers starting at 1, so\n"
            "-- literal ids like `WHERE user_id = 42` keep working at every scale.\n"
            "SELECT count(*) FROM information_schema.tables;\n"
        )
    console.print(f"[green]✓[/green] Created {config_path}")
    console.print(f"[green]✓[/green] Created {example}")
    console.print()
    console.print("Next steps:")
    console.print("  1. export DATABASE_URL=postgresql://user:pass@host:5432/dbname")
    console.print(f"  2. Edit {config_path} (scale targets, queries, thresholds)")
    console.print("  3. dbscale run")


@app.command()
def inspect(
    config: str = CONFIG_OPTION,
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show columns, statistics and indexes"),
) -> None:
    """Connect read-only to the source database and show the schema DBScale will reproduce."""
    from dbscale.experiments import ExperimentRunner
    from dbscale.reporting import render_schema

    cfg = _load(config)
    runner = ExperimentRunner(cfg, ConsoleProgress(console))
    try:
        schema, _ = runner.inspect()
    except Exception as exc:  # noqa: BLE001
        _die(exc)
    render_schema(schema, console, verbose=verbose)
    from dbscale.core.scale import resolve_scale

    plan = resolve_scale(cfg.scale.parsed_targets(), schema, cfg.scale.base_rows)
    console.print("Scale targets:")
    for t in plan.targets:
        from dbscale.core.scale import format_count

        console.print(f"  {t.label:<12} ~{format_count(t.total_rows)} rows total")
    console.print()


@app.command()
def run(
    config: str = CONFIG_OPTION,
    output: str | None = typer.Option(None, "--json", "-o", help="Write machine-readable results here"),
    keep_sandbox: bool = typer.Option(False, "--keep-sandbox", help="Do not destroy the sandbox afterwards"),
    no_ai: bool = typer.Option(False, "--no-ai", help="Disable AI analysis even if enabled in config"),
    ai: bool = typer.Option(False, "--ai", help="Enable AI analysis even if disabled in config"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show every finding with evidence"),
    fail_on: str | None = typer.Option(
        None,
        "--fail-on",
        help="Exit non-zero if any finding reaches this severity (low|medium|high|critical)",
    ),
) -> None:
    """Run the experiment: inspect, reproduce, scale, benchmark, analyze."""
    from dbscale.experiments import ExperimentRunner
    from dbscale.reporting import render_report, write_result

    cfg = _load(config)
    if keep_sandbox:
        cfg.sandbox.keep = True
    if no_ai:
        cfg.ai.enabled = False
    if ai:
        cfg.ai.enabled = True
    threshold = _parse_severity(fail_on)

    console.print()
    runner = ExperimentRunner(cfg, ConsoleProgress(console))
    try:
        result = runner.run()
    except ConfigError as exc:
        err_console.print(f"[red]Configuration error:[/red] {exc}")
        raise typer.Exit(code=2) from None
    except KeyboardInterrupt:
        err_console.print(
            "\n[yellow]Interrupted.[/yellow] Sandbox has been destroyed unless --keep-sandbox was set."
        )
        raise typer.Exit(code=130) from None
    except Exception as exc:  # noqa: BLE001
        _die(exc)

    render_report(result, console, verbose=verbose)
    out_path = output or cfg.output.json_path
    if out_path:
        base = cfg.base_dir or Path(".")
        path = Path(out_path) if Path(out_path).is_absolute() else base / out_path
        write_result(result, path)
        console.print(f"Results written to [bold]{path}[/bold]")
        console.print()

    if threshold is not None and result.risk is not None and result.risk.rank >= threshold.rank:
        err_console.print(
            f"[red]Findings at or above '{threshold.value}' severity: failing as requested.[/red]"
        )
        raise typer.Exit(code=3)


@app.command()
def report(
    results: Path = typer.Argument(Path("dbscale-results.json"), help="Results file from a previous run"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show every finding with evidence"),
) -> None:
    """Render a saved results file."""
    from dbscale.reporting import load_result, render_report

    if not results.exists():
        err_console.print(f"[red]Not found:[/red] {results}")
        raise typer.Exit(code=2)
    render_report(load_result(results), console, verbose=verbose)


@app.command()
def explain(
    results: Path = typer.Argument(Path("dbscale-results.json"), help="Results file from a previous run"),
    config: str = CONFIG_OPTION,
    save: bool = typer.Option(
        True, "--save/--no-save", help="Store the AI analysis back into the results file"
    ),
    provider: str | None = typer.Option(
        None, "--provider", help="Override ai.provider (openai, anthropic, ollama, ...)"
    ),
    model: str | None = typer.Option(None, "--model", help="Override ai.model"),
) -> None:
    """Ask the configured LLM to interpret a saved results file (no database access needed)."""
    from dbscale.experiments import run_ai_analysis
    from dbscale.reporting import load_result, render_report, write_result

    cfg = _load(config)
    if not results.exists():
        err_console.print(f"[red]Not found:[/red] {results}")
        raise typer.Exit(code=2)
    result = load_result(results)
    cfg.ai.enabled = True
    if provider:
        cfg.ai.provider = provider
    if model:
        cfg.ai.model = model
    result.ai = run_ai_analysis(result, cfg, ConsoleProgress(console))
    render_report(result, console)
    if save and result.ai and not result.ai.error:
        write_result(result, results)
        console.print(f"AI analysis saved to [bold]{results}[/bold]")


def _parse_severity(value: str | None) -> Severity | None:
    if value is None:
        return None
    try:
        return Severity(value.lower())
    except ValueError:
        err_console.print(f"[red]Invalid --fail-on value:[/red] {value} (use low, medium, high or critical)")
        raise typer.Exit(code=2) from None


def _die(exc: BaseException) -> None:
    err_console.print(f"\n[red]Error:[/red] {exc}")
    raise typer.Exit(code=1)


def main() -> None:
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
