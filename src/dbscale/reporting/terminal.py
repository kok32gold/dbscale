"""Concise terminal report for developers."""

from __future__ import annotations

from rich.console import Console
from rich.table import Table as RichTable
from rich.text import Text

from dbscale.core.experiment import ExperimentResult, WorkloadResult
from dbscale.core.findings import Severity
from dbscale.core.recommendations import Recommendation
from dbscale.core.scale import format_count
from dbscale.core.schema import Schema

_SEV_STYLE = {
    Severity.INFO: "dim",
    Severity.LOW: "cyan",
    Severity.MEDIUM: "yellow",
    Severity.HIGH: "bold red",
    Severity.CRITICAL: "bold white on red",
}

RULE = "─" * 60


def fmt_ms(ms: float | None) -> str:
    if ms is None:
        return "—"
    if ms >= 1000:
        return f"{ms / 1000:.2f}s"
    if ms >= 10:
        return f"{ms:.0f}ms"
    return f"{ms:.1f}ms"


def sev_text(sev: Severity, label: str | None = None) -> Text:
    return Text((label or sev.value).upper(), style=_SEV_STYLE[sev])


def render_report(result: ExperimentResult, console: Console, *, verbose: bool = False) -> None:
    console.print()
    console.print(Text("DBScale", style="bold"))
    console.print()
    console.print(f"Experiment: [bold]{result.name}[/bold]")
    db = result.database
    console.print(f"Database:   {_db_label(db.type)} {db.version or ''}".rstrip())
    console.print(
        f"Source:     {db.source_tables} tables, ~{format_count(db.source_rows)} rows (inspected read-only)"
    )
    tested = " · ".join(f"{t.label} (~{format_count(t.total_rows)})" for t in result.scale.targets)
    console.print(f"Tested:     {tested}")
    if result.metadata.get("generation_notes") and verbose:
        console.print()
        for note in result.metadata["generation_notes"]:
            console.print(f"[dim]note: {note}[/dim]")

    console.print()
    console.print(RULE)
    risks = [w for w in result.workloads if w.risk and w.risk.rank >= Severity.MEDIUM.rank]
    failures = [w for w in result.workloads if any(m.error for m in w.measurements)]
    if not result.workloads:
        console.print("No workloads were measured.")
    elif risks or failures:
        n = len({w.query.name for w in risks + failures})
        console.print(Text(f"{n} performance risk{'s' if n != 1 else ''} found", style="bold"))
    else:
        console.print(Text("No performance risks found at the tested scales", style="bold green"))

    for w in sorted(result.workloads, key=lambda w: -(w.risk.rank if w.risk else -1)):
        console.print(RULE)
        _render_workload(w, result, console, verbose=verbose)

    if result.ai is not None:
        console.print(RULE)
        _render_ai(result, console)
    console.print(RULE)


def _render_workload(w: WorkloadResult, result: ExperimentResult, console: Console, *, verbose: bool) -> None:
    header = Text()
    header.append("QUERY  ", style="dim")
    header.append(w.query.name, style="bold")
    header.append("  ")
    if w.risk is None or w.risk.rank < Severity.MEDIUM.rank:
        header.append("✓ OK", style="green")
    else:
        header.append("⚠ ", style=_SEV_STYLE[w.risk])
        header.append(f"{w.risk.value.upper()} RISK", style=_SEV_STYLE[w.risk])
    console.print(header)
    console.print()

    table = RichTable.grid(padding=(0, 3))
    table.add_column(justify="left", min_width=10)
    table.add_column(justify="right", min_width=8)
    table.add_column(justify="left", style="dim")
    for m in w.measurements:
        if m.error:
            table.add_row(m.scale_label, Text("failed", style="red"), m.error[:90])
            continue
        lat = m.latency
        detail = ""
        if lat:
            detail = f"p95 {fmt_ms(lat.p95_ms)}"
        if m.plan and m.plan.rows_scanned is not None:
            detail += f" · {m.plan.rows_scanned:,} rows scanned → {m.rows_returned or 0:,} returned"
        elif m.plan and m.plan.rows_scanned is None:
            detail += " · rows scanned unavailable"
        table.add_row(m.scale_label, fmt_ms(lat.p50_ms if lat else None), detail)
    console.print(table)

    if w.scaling and w.scaling.exponent is not None and len(w.successful) >= 2:
        exp = w.scaling.exponent
        if exp >= 1.15:
            trend = "grows faster than the data"
        elif exp >= 0.6:
            trend = "grows roughly linearly with the data"
        elif exp >= 0.2:
            trend = "grows slowly with the data"
        else:
            trend = "is flat as the data grows"
        line = f"\nLatency {trend} (p50 ~ data^{exp:.2f})."
        if w.scaling.projected_next_10x_ms is not None and exp >= 0.6:
            line += f" Projected at another 10x: ~{fmt_ms(w.scaling.projected_next_10x_ms)}."
        console.print(Text(line, style="italic"))

    if w.findings:
        console.print()
        console.print(Text("Findings", style="bold"))
        shown = w.findings if verbose else [f for f in w.findings if f.severity.rank >= Severity.LOW.rank][:4]
        for f in shown:
            row = Text("  ")
            row.append(f"{f.severity.value.upper():<9}", style=_SEV_STYLE[f.severity])
            row.append(f.title)
            console.print(row)
            console.print(f"           [dim]{f.description}[/dim]")
            if verbose and f.evidence:
                for k, v in f.evidence.items():
                    if v is None:
                        continue
                    console.print(f"           [dim]{k}: {_fmt_value(v)}[/dim]")
        hidden = len(w.findings) - len(shown)
        if hidden > 0:
            console.print(f"           [dim]+{hidden} more (use --verbose)[/dim]")

    if w.recommendations:
        console.print()
        console.print(Text("Recommended actions", style="bold"))
        for r in w.recommendations if verbose else w.recommendations[:3]:
            _render_recommendation(r, console)
    console.print()


def _render_recommendation(r: Recommendation, console: Console) -> None:
    line = Text("  ")
    line.append(f"{r.severity.value.upper():<9}", style=_SEV_STYLE[r.severity])
    line.append(r.title, style="bold")
    line.append(f"  [{r.type.value}]", style="dim")
    console.print(line)
    console.print(f"           {r.explanation}")
    if r.proposed_action:
        console.print(f"           [green]{r.proposed_action}[/green]")
    meta = f"expected impact: {r.expected_impact.value} · confidence: {int(r.confidence * 100)}%"
    if r.source == "llm":
        meta += " · source: AI"
    console.print(f"           [dim]{meta}[/dim]")
    if r.tradeoffs:
        console.print(f"           [dim]tradeoff: {r.tradeoffs}[/dim]")


def _render_ai(result: ExperimentResult, console: Console) -> None:
    ai = result.ai
    assert ai is not None
    console.print(Text(f"AI analysis ({ai.provider}/{ai.model})", style="bold"))
    console.print()
    if ai.error:
        console.print(f"[red]{ai.error}[/red]")
        if ai.summary:
            console.print(ai.summary)
        return
    if ai.summary:
        console.print(ai.summary)
    if ai.recommendations:
        console.print()
        console.print(Text("AI recommendations", style="bold"))
        for r in ai.recommendations:
            if r.query_name:
                console.print(f"  [dim]{r.query_name}[/dim]")
            _render_recommendation(r, console)
    if ai.hypotheses:
        console.print()
        console.print(Text("Hypotheses (not proven by the evidence)", style="bold"))
        for h in ai.hypotheses:
            console.print(f"  • {h}")
    if ai.follow_up_experiments:
        console.print()
        console.print(Text("Suggested follow-up experiments", style="bold"))
        for e in ai.follow_up_experiments:
            console.print(f"  • {e}")
    console.print()


def render_schema(schema: Schema, console: Console, *, verbose: bool = False) -> None:
    console.print()
    console.print(
        Text(f"{_db_label(schema.database_type)} {schema.database_version or ''}".rstrip(), style="bold")
    )
    console.print(f"{len(schema.tables)} tables, ~{format_count(schema.total_rows)} rows")
    console.print()
    table = RichTable(show_header=True, header_style="bold", box=None, padding=(0, 2))
    table.add_column("Table")
    table.add_column("Rows", justify="right")
    table.add_column("Columns", justify="right")
    table.add_column("Indexes", justify="right")
    table.add_column("References")
    for t in schema.topological_order():
        refs = ", ".join(sorted({fk.referenced_table for fk in t.foreign_keys})) or "—"
        table.add_row(
            t.qualified_name if t.schema_name not in (None, "public") else t.name,
            format_count(t.estimated_rows),
            str(len(t.columns)),
            str(len([ix for ix in t.indexes if not ix.primary])),
            refs,
        )
    console.print(table)
    if verbose:
        for t in schema.tables:
            console.print()
            console.print(Text(t.name, style="bold"))
            for c in t.columns:
                flags = []
                if c.name in t.primary_key:
                    flags.append("PK")
                if t.foreign_key_for(c.name):
                    flags.append("FK")
                if not c.nullable:
                    flags.append("NOT NULL")
                stats = ""
                if c.stats:
                    bits = []
                    if c.stats.distinct_count is not None:
                        bits.append(f"distinct≈{format_count(c.stats.distinct_count)}")
                    if c.stats.null_fraction:
                        bits.append(f"null {c.stats.null_fraction:.0%}")
                    stats = "  " + ", ".join(bits) if bits else ""
                console.print(f"  {c.name:<28} {c.native_type:<24} {' '.join(flags)}[dim]{stats}[/dim]")
            for ix in t.indexes:
                if not ix.primary:
                    console.print(
                        f"  [dim]index {ix.name} ({', '.join(ix.columns)}){' UNIQUE' if ix.unique else ''}[/dim]"
                    )
    console.print()


def _db_label(t: str) -> str:
    return {"postgres": "PostgreSQL"}.get(t, t)


def _fmt_value(v: object) -> str:
    if isinstance(v, float):
        return f"{v:,.3f}" if abs(v) < 1 else f"{v:,.1f}"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)
