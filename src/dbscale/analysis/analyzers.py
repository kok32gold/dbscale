"""Rule-based analyzers. Each inspects measurements and emits findings with evidence.

Analyzers are pure functions of the measurements, the schema and the
thresholds. They never propose actions and never call an LLM.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from dbscale.analysis.scaling import compute_scaling, fit_exponent
from dbscale.core.experiment import ScalingSummary, ThresholdSpec, WorkloadResult
from dbscale.core.findings import Finding, FindingType, Severity
from dbscale.core.measurements import NodeKind, PlanSummary, QueryMeasurement, ScanInfo
from dbscale.core.scale import ScalePlan
from dbscale.core.schema import Schema, Table

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SQL_NOISE = {
    "and",
    "or",
    "not",
    "is",
    "null",
    "true",
    "false",
    "text",
    "numeric",
    "integer",
    "bigint",
    "interval",
    "timestamp",
    "date",
    "now",
    "without",
    "time",
    "zone",
    "with",
    "any",
    "all",
    "in",
    "like",
    "ilike",
    "between",
    "case",
    "when",
    "then",
    "else",
    "end",
    "character",
    "varying",
    "boolean",
    "uuid",
    "int4",
    "int8",
    "float8",
    "jsonb",
    "json",
    "lower",
    "upper",
    "coalesce",
    "array",
    "to",
    "from",
    "cast",
}

MIN_SEQ_SCAN_ROWS = 10_000
MIN_SORT_ROWS = 100_000
MIN_AGG_ROWS = 1_000_000
MIN_EXCESS_SCAN_ROWS = 100_000
EXCESS_SCAN_RATIO = 1_000
HIGHLY_SELECTIVE = 0.001
SUPERLINEAR_EXPONENT = 1.15
GROWING_EXPONENT = 0.6
MIN_INTERESTING_LATENCY_MS = 50.0


@dataclass
class AnalysisContext:
    schema: Schema
    result: WorkloadResult
    thresholds: ThresholdSpec = field(default_factory=ThresholdSpec)
    scale: ScalePlan | None = None

    @property
    def query_name(self) -> str:
        return self.result.query.name

    def largest(self) -> QueryMeasurement | None:
        """Largest-scale successful measurement that has a plan."""
        candidates = [m for m in self.result.successful if m.plan is not None]
        return max(candidates, key=lambda m: m.scale_factor) if candidates else None

    def table_for(self, relation: str | None) -> Table | None:
        return self.schema.table(relation) if relation else None

    def p95_threshold_breached(self, m: QueryMeasurement) -> bool:
        return bool(m.latency and self.thresholds.p95_ms and m.latency.p95_ms > self.thresholds.p95_ms)


class Analyzer(ABC):
    name: str = "analyzer"

    @abstractmethod
    def analyze(self, ctx: AnalysisContext) -> list[Finding]: ...


def _finding(
    ctx: AnalysisContext, type_: FindingType, severity: Severity, title: str, description: str, **kw: Any
) -> Finding:
    return Finding(
        id="",
        type=type_,
        severity=severity,
        title=title,
        description=description,
        query_name=ctx.query_name,
        **kw,
    )


def _bump(sev: Severity) -> Severity:
    order = [Severity.INFO, Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL]
    return order[min(order.index(sev) + 1, len(order) - 1)]


def _lower(sev: Severity) -> Severity:
    order = [Severity.INFO, Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL]
    return order[max(order.index(sev) - 1, 0)]


def _rows_severity(rows: int) -> Severity:
    if rows >= 10_000_000:
        return Severity.HIGH
    if rows >= 1_000_000:
        return Severity.MEDIUM
    if rows >= 100_000:
        return Severity.LOW
    return Severity.INFO


_STRING_LITERAL_RE = re.compile(r"'(?:[^']|'')*'")
_MAYBE_QUALIFIED_RE = re.compile(r'(?:"?([A-Za-z_][A-Za-z0-9_]*)"?\.)?"?([A-Za-z_][A-Za-z0-9_]*)"?')


def extract_columns(expression: str | None, table: Table | None, alias: str | None = None) -> list[str]:
    """Column names of ``table`` referenced in a native filter/sort/join expression, in order.

    Unqualified identifiers are accepted if they are columns of the table. Qualified identifiers
    (``o.user_id``) are accepted only when the prefix is the table's alias or name, so the other
    side of a join condition is not mistaken for a local column.
    """
    if not expression or table is None:
        return []
    accepted_prefixes = {p for p in (alias, table.name) if p}
    names = set(table.column_names())
    cleaned = _STRING_LITERAL_RE.sub("''", expression)
    out: list[str] = []
    for prefix, ident in _MAYBE_QUALIFIED_RE.findall(cleaned):
        if ident not in names or ident.lower() in _SQL_NOISE or ident in out:
            continue
        if prefix and prefix not in accepted_prefixes:
            continue
        out.append(ident)
    return out


def _fmt_ms(ms: float) -> str:
    return f"{ms / 1000:.2f}s" if ms >= 1000 else f"{ms:.0f}ms"


# --------------------------------------------------------------------------- analyzers


class SequentialScanAnalyzer(Analyzer):
    name = "sequential_scan"

    def analyze(self, ctx: AnalysisContext) -> list[Finding]:
        m = ctx.largest()
        if m is None or m.plan is None:
            return []
        worst: dict[str, ScanInfo] = {}
        for scan in m.plan.seq_scans:
            if scan.rows_scanned < MIN_SEQ_SCAN_ROWS:
                continue
            if scan.relation not in worst or scan.rows_scanned > worst[scan.relation].rows_scanned:
                worst[scan.relation] = scan
        findings = []
        for scan in worst.values():
            table = ctx.table_for(scan.relation)
            columns = extract_columns(scan.filter, table, scan.alias)
            selectivity = scan.rows_output / scan.rows_scanned if scan.rows_scanned else None
            # Sort keys on this table (even small sorts) let an index serve ORDER BY ... LIMIT as well.
            order_by: list[str] = []
            for sort in m.plan.sorts:
                sort_table, sort_cols = _sort_target(ctx, sort.keys, m.plan)
                if sort_table == scan.relation:
                    order_by += [c for c in sort_cols if c not in order_by]
            severity = _rows_severity(scan.rows_scanned)
            if scan.filter is None:
                severity = _lower(severity)
            elif (
                selectivity is not None
                and selectivity < HIGHLY_SELECTIVE
                and scan.rows_scanned >= MIN_EXCESS_SCAN_ROWS
            ):
                # Reading 100K+ rows to keep <0.1% is the textbook missing index; it only gets worse.
                severity = _bump(severity)
            if ctx.p95_threshold_breached(m):
                severity = _bump(severity)
            if severity == Severity.INFO and scan.rows_scanned < MIN_EXCESS_SCAN_ROWS:
                continue
            if scan.filter:
                desc = (
                    f"At {m.scale_label}, '{scan.relation}' is read sequentially: {scan.rows_scanned:,} rows scanned "
                    f"to keep {scan.rows_output:,} ({scan.rows_removed_by_filter:,} discarded by the filter)."
                )
            else:
                desc = (
                    f"At {m.scale_label}, '{scan.relation}' is read in full ({scan.rows_scanned:,} rows) "
                    f"without a filter; cost grows linearly with table size."
                )
            findings.append(
                _finding(
                    ctx,
                    FindingType.SEQUENTIAL_SCAN,
                    severity,
                    f"Sequential scan on {scan.relation}",
                    desc,
                    scale_label=m.scale_label,
                    table=scan.relation,
                    columns=columns,
                    evidence={
                        "rows_scanned": scan.rows_scanned,
                        "rows_output": scan.rows_output,
                        "rows_removed_by_filter": scan.rows_removed_by_filter,
                        "rows_returned": m.rows_returned,
                        "selectivity": selectivity,
                        "loops": scan.loops,
                        "filter": scan.filter,
                        "order_by_columns": order_by,
                        "scan_time_ms": round(scan.time_ms, 2),
                        "latency_p50_ms": m.latency.p50_ms if m.latency else None,
                        "latency_p95_ms": m.latency.p95_ms if m.latency else None,
                    },
                )
            )
        return findings


class ExcessiveRowsScannedAnalyzer(Analyzer):
    name = "excessive_rows_scanned"

    def analyze(self, ctx: AnalysisContext) -> list[Finding]:
        m = ctx.largest()
        if m is None or m.plan is None:
            return []
        scanned = m.plan.rows_scanned
        if scanned is None:
            return []
        returned = max(m.rows_returned or m.plan.rows_returned or 0, 1)
        ratio = scanned / returned
        if scanned < MIN_EXCESS_SCAN_ROWS or ratio < EXCESS_SCAN_RATIO:
            return []
        severity = _rows_severity(scanned)
        if severity == Severity.INFO:
            severity = Severity.LOW
        tables = sorted({s.relation for s in m.plan.scans})
        return [
            _finding(
                ctx,
                FindingType.EXCESSIVE_ROWS_SCANNED,
                severity,
                "Scans far more rows than it returns",
                f"At {m.scale_label} the query touches {scanned:,} rows to return {returned:,} "
                f"({ratio:,.0f}:1). Work is dominated by rows that are discarded.",
                scale_label=m.scale_label,
                table=tables[0] if len(tables) == 1 else None,
                evidence={
                    "rows_scanned": scanned,
                    "rows_returned": returned,
                    "ratio": round(ratio, 1),
                    "tables": tables,
                    "latency_p50_ms": m.latency.p50_ms if m.latency else None,
                },
            )
        ]


class LargeSortAnalyzer(Analyzer):
    name = "large_sort"

    def analyze(self, ctx: AnalysisContext) -> list[Finding]:
        m = ctx.largest()
        if m is None or m.plan is None:
            return []
        findings = []
        for sort in m.plan.sorts:
            on_disk = (sort.space_type or "").lower() == "disk" or (sort.method or "").lower().startswith(
                "external"
            )
            if sort.rows < MIN_SORT_ROWS and not on_disk:
                continue
            severity = (
                Severity.HIGH if on_disk else (Severity.MEDIUM if sort.rows >= 1_000_000 else Severity.LOW)
            )
            table, columns = _sort_target(ctx, sort.keys, m.plan)
            where = "on disk" if on_disk else "in memory"
            findings.append(
                _finding(
                    ctx,
                    FindingType.LARGE_SORT,
                    severity,
                    f"Large sort ({sort.rows:,} rows, {where})",
                    f"At {m.scale_label} the query sorts {sort.rows:,} rows by {', '.join(sort.keys) or 'unknown keys'} "
                    f"{where}"
                    + (f" using {sort.space_kb:,} kB" if sort.space_kb else "")
                    + ". Sort cost grows as n·log(n).",
                    scale_label=m.scale_label,
                    table=table,
                    columns=columns,
                    evidence={
                        "sort_keys": sort.keys,
                        "rows_sorted": sort.rows,
                        "method": sort.method,
                        "space_kb": sort.space_kb,
                        "space_type": sort.space_type,
                        "sort_time_ms": round(sort.time_ms, 2),
                        "rows_returned": m.rows_returned,
                    },
                )
            )
        return findings


def _sort_target(
    ctx: AnalysisContext, keys: list[str], plan: PlanSummary | None = None
) -> tuple[str | None, list[str]]:
    """Resolve sort keys like 'orders.created_at DESC', 'o.created_at' or 'created_at' to (table, columns).

    Ambiguous bare names are narrowed to tables actually scanned by the plan, then by alias.
    """
    columns: list[str] = []
    tables: set[str] = set()
    scanned: dict[str, str] = {}  # alias/relation -> relation
    if plan is not None:
        for s in plan.scans:
            scanned[s.relation] = s.relation
            if s.alias:
                scanned[s.alias] = s.relation
    for key in keys:
        ident = key.split()[0] if key else ""
        ident = ident.strip('()"')
        parts = ident.split(".")
        col = parts[-1].strip('"')
        prefix = parts[-2].strip('"') if len(parts) > 1 else None
        candidates = [t for t in ctx.schema.tables if t.column(col) is not None]
        if prefix and prefix in scanned and ctx.schema.table(scanned[prefix]):
            candidates = [ctx.schema.table(scanned[prefix])]  # type: ignore[list-item]
        elif prefix and ctx.schema.table(prefix):
            candidates = [ctx.schema.table(prefix)]  # type: ignore[list-item]
        elif len(candidates) > 1 and scanned:
            narrowed = [t for t in candidates if t.name in scanned.values()]
            candidates = narrowed or candidates
        if len(candidates) > 1 and prefix:
            narrowed = [t for t in candidates if t.name.lower().startswith(prefix.lower())]
            candidates = narrowed or candidates
        if len(candidates) == 1:
            tables.add(candidates[0].name)
            if col not in columns:
                columns.append(col)
    return (tables.pop() if len(tables) == 1 else None), columns


class ExpensiveJoinAnalyzer(Analyzer):
    name = "expensive_join"

    def analyze(self, ctx: AnalysisContext) -> list[Finding]:
        m = ctx.largest()
        if m is None or m.plan is None:
            return []
        findings = []
        for join in m.plan.joins:
            inner_work = max(join.inner_rows_scanned, join.inner_rows)
            severity: Severity | None = None
            reason = ""
            if (
                join.kind == NodeKind.NESTED_LOOP
                and join.inner_kind == NodeKind.SEQ_SCAN
                and join.inner_loops >= 10
            ):
                severity = Severity.HIGH if inner_work >= 1_000_000 else Severity.MEDIUM
                reason = (
                    f"a nested loop re-scans '{join.inner_relation}' sequentially {join.inner_loops:,} times "
                    f"({inner_work:,} rows read in total)"
                )
            elif join.kind == NodeKind.NESTED_LOOP and inner_work >= 10_000_000:
                severity = Severity.MEDIUM
                reason = (
                    f"a nested loop reads {inner_work:,} inner rows across {join.inner_loops:,} iterations"
                )
            elif join.kind == NodeKind.HASH_JOIN and (join.hash_batches or 1) > 1:
                severity = Severity.MEDIUM
                reason = (
                    f"the hash table for '{join.inner_relation or 'the inner side'}' did not fit in work_mem "
                    f"and was split into {join.hash_batches} batches (spilled to disk)"
                )
            elif join.rows >= 10_000_000:
                severity = Severity.MEDIUM
                reason = f"the join produces {join.rows:,} rows"
            if severity is None:
                continue
            table = join.inner_relation
            inner_table = ctx.table_for(table)
            # Prefer the inner scan's own filter/index condition (it names the join key on that side),
            # then the join condition restricted to the inner alias.
            inner_scan = next(
                (
                    s
                    for s in m.plan.scans
                    if s.relation == table and (s.alias == join.inner_alias or join.inner_alias is None)
                ),
                None,
            )
            columns = extract_columns(join.condition, inner_table, join.inner_alias)
            if inner_scan is not None:
                scan_cols = extract_columns(
                    inner_scan.index_condition or inner_scan.filter, inner_table, inner_scan.alias
                )
                columns = scan_cols or columns
            findings.append(
                _finding(
                    ctx,
                    FindingType.EXPENSIVE_JOIN,
                    severity,
                    f"Expensive {join.kind.value.replace('_', ' ')}",
                    f"At {m.scale_label}, {reason}.",
                    scale_label=m.scale_label,
                    table=table,
                    columns=columns,
                    evidence={
                        "join_kind": join.kind.value,
                        "join_type": join.join_type,
                        "condition": join.condition,
                        "rows_out": join.rows,
                        "outer_rows": join.outer_rows,
                        "inner_rows": join.inner_rows,
                        "inner_rows_scanned": join.inner_rows_scanned,
                        "inner_loops": join.inner_loops,
                        "inner_scan": join.inner_kind.value if join.inner_kind else None,
                        "inner_relation": join.inner_relation,
                        "hash_batches": join.hash_batches,
                        "join_time_ms": round(join.time_ms, 2),
                    },
                )
            )
        return findings


class AggregationAnalyzer(Analyzer):
    name = "aggregation_bottleneck"

    def analyze(self, ctx: AnalysisContext) -> list[Finding]:
        m = ctx.largest()
        if m is None or m.plan is None:
            return []
        findings = []
        for agg in m.plan.aggregates:
            spilled = bool(agg.disk_kb)
            if agg.input_rows < MIN_AGG_ROWS and not spilled:
                continue
            severity = Severity.HIGH if spilled or agg.input_rows >= 10_000_000 else Severity.MEDIUM
            keys = agg.group_keys or []
            table, columns = _sort_target(ctx, keys, m.plan)
            findings.append(
                _finding(
                    ctx,
                    FindingType.AGGREGATION_BOTTLENECK,
                    severity,
                    f"Aggregates {agg.input_rows:,} rows into {agg.output_rows:,}",
                    f"At {m.scale_label} the query aggregates {agg.input_rows:,} rows"
                    + (f" grouped by {', '.join(keys)}" if keys else "")
                    + (f", spilling {agg.disk_kb:,} kB to disk" if spilled else "")
                    + ". Every query recomputes this from scratch.",
                    scale_label=m.scale_label,
                    table=table,
                    columns=columns,
                    evidence={
                        "strategy": agg.strategy,
                        "group_keys": keys,
                        "input_rows": agg.input_rows,
                        "output_rows": agg.output_rows,
                        "disk_kb": agg.disk_kb,
                        "aggregate_time_ms": round(agg.time_ms, 2),
                    },
                )
            )
        return findings


class ScalingAnalyzer(Analyzer):
    name = "scaling"

    def analyze(self, ctx: AnalysisContext) -> list[Finding]:
        ok = sorted(ctx.result.successful, key=lambda m: m.scale_factor)
        points = [(m.scale_factor, m.latency.p50_ms) for m in ok if m.latency]
        exponent = fit_exponent(points)
        if exponent is None:
            return []
        last = ok[-1]
        last_p50 = last.latency.p50_ms if last.latency else 0.0
        if last_p50 < MIN_INTERESTING_LATENCY_MS:
            return []
        superlinear_at = ctx.thresholds.max_scaling_exponent or SUPERLINEAR_EXPONENT
        series = {m.scale_label: round(m.latency.p50_ms, 2) for m in ok if m.latency}
        first_p50 = ok[0].latency.p50_ms if ok[0].latency else None
        evidence = {
            "exponent": round(exponent, 3),
            "p50_ms_by_scale": series,
            "factor_by_scale": {m.scale_label: round(m.scale_factor, 3) for m in ok},
            "projected_next_10x_ms": round(last_p50 * (10**exponent), 1),
        }
        growth = (
            f"{_fmt_ms(first_p50)} → {_fmt_ms(last_p50)} from {ok[0].scale_label} to {last.scale_label}"
            if first_p50 is not None
            else ""
        )
        if exponent >= superlinear_at:
            severity = Severity.HIGH
            if ctx.thresholds.p95_ms and last.latency and last.latency.p95_ms > 5 * ctx.thresholds.p95_ms:
                severity = Severity.CRITICAL
            return [
                _finding(
                    ctx,
                    FindingType.NON_LINEAR_SCALING,
                    severity,
                    "Latency grows faster than the data",
                    f"Latency scales as data^{exponent:.2f} ({growth}). Another 10x in data would take roughly "
                    f"{_fmt_ms(last_p50 * 10**exponent)}.",
                    scale_label=last.scale_label,
                    evidence=evidence,
                )
            ]
        if exponent >= GROWING_EXPONENT:
            severity = Severity.MEDIUM if last_p50 >= 200 else Severity.LOW
            return [
                _finding(
                    ctx,
                    FindingType.GROWING_LATENCY,
                    severity,
                    "Latency grows with data size",
                    f"Latency scales roughly linearly with data (exponent {exponent:.2f}; {growth}). "
                    f"The query reads an amount of data proportional to table size.",
                    scale_label=last.scale_label,
                    evidence=evidence,
                )
            ]
        return []


class ThresholdAnalyzer(Analyzer):
    name = "threshold"

    def analyze(self, ctx: AnalysisContext) -> list[Finding]:
        t = ctx.thresholds
        findings: list[Finding] = []
        breaches: list[dict[str, Any]] = []
        worst_ratio = 0.0
        worst: QueryMeasurement | None = None
        for m in ctx.result.successful:
            if not m.latency:
                continue
            for metric, limit in (("p95_ms", t.p95_ms), ("p99_ms", t.p99_ms)):
                if limit is None:
                    continue
                value = getattr(m.latency, metric)
                if value > limit:
                    ratio = value / limit
                    breaches.append(
                        {
                            "scale": m.scale_label,
                            "metric": metric,
                            "value_ms": round(value, 2),
                            "limit_ms": limit,
                        }
                    )
                    if ratio > worst_ratio:
                        worst_ratio, worst = ratio, m
        if breaches and worst is not None:
            severity = (
                Severity.CRITICAL
                if worst_ratio >= 4
                else Severity.HIGH
                if worst_ratio >= 2
                else Severity.MEDIUM
            )
            first = min(breaches, key=lambda b: ctx.result.measurement(b["scale"]).scale_factor)  # type: ignore[union-attr]
            findings.append(
                _finding(
                    ctx,
                    FindingType.THRESHOLD_BREACH,
                    severity,
                    f"Exceeds {first['metric'].replace('_ms', '')} threshold from {first['scale']}",
                    f"{first['metric'].replace('_ms', '')} is {_fmt_ms(first['value_ms'])} at {first['scale']} "
                    f"(limit {_fmt_ms(first['limit_ms'])}); worst is {worst_ratio:.1f}x over the limit at {worst.scale_label}.",
                    scale_label=worst.scale_label,
                    evidence={"breaches": breaches, "worst_ratio": round(worst_ratio, 2)},
                )
            )
        if t.max_rows_scanned is not None:
            largest = ctx.largest()
            if (
                largest
                and largest.plan
                and largest.plan.rows_scanned is not None
                and largest.plan.rows_scanned > t.max_rows_scanned
            ):
                findings.append(
                    _finding(
                        ctx,
                        FindingType.THRESHOLD_BREACH,
                        Severity.HIGH,
                        "Exceeds rows-scanned threshold",
                        f"At {largest.scale_label} the query scans {largest.plan.rows_scanned:,} rows (limit {t.max_rows_scanned:,}).",
                        scale_label=largest.scale_label,
                        evidence={"rows_scanned": largest.plan.rows_scanned, "limit": t.max_rows_scanned},
                    )
                )
        return findings


class ExecutionFailureAnalyzer(Analyzer):
    name = "execution_failure"

    def analyze(self, ctx: AnalysisContext) -> list[Finding]:
        failed = [m for m in ctx.result.measurements if m.error]
        if not failed:
            return []
        first = min(failed, key=lambda m: m.scale_factor)
        timeout = "timeout" in (first.error or "").lower()
        return [
            _finding(
                ctx,
                FindingType.EXECUTION_FAILURE,
                Severity.CRITICAL if timeout else Severity.HIGH,
                "Query timed out" if timeout else "Query failed",
                f"At {first.scale_label} the query {'exceeded its timeout' if timeout else 'failed'}: {first.error}",
                scale_label=first.scale_label,
                evidence={"failed_scales": [m.scale_label for m in failed], "error": first.error},
            )
        ]


def _dedupe(findings: list[Finding]) -> list[Finding]:
    """Drop findings that restate another finding's evidence.

    'Scans far more rows than it returns' is implied when a sequential scan finding
    already covers every scanned table.
    """
    seq_tables = {f.table for f in findings if f.type == FindingType.SEQUENTIAL_SCAN and f.table}
    out = []
    for f in findings:
        if f.type == FindingType.EXCESSIVE_ROWS_SCANNED:
            tables = set(f.evidence.get("tables") or [])
            if tables and tables <= seq_tables:
                continue
        out.append(f)
    return out


DEFAULT_ANALYZERS: list[Analyzer] = [
    ExecutionFailureAnalyzer(),
    SequentialScanAnalyzer(),
    ExcessiveRowsScannedAnalyzer(),
    LargeSortAnalyzer(),
    ExpensiveJoinAnalyzer(),
    AggregationAnalyzer(),
    ScalingAnalyzer(),
    ThresholdAnalyzer(),
]


def analyze_workload(
    ctx: AnalysisContext, analyzers: list[Analyzer] | None = None
) -> tuple[list[Finding], ScalingSummary]:
    """Run all analyzers for one workload. Finding IDs are assigned deterministically."""
    findings: list[Finding] = []
    for analyzer in analyzers or DEFAULT_ANALYZERS:
        findings.extend(analyzer.analyze(ctx))
    findings = _dedupe(findings)
    findings.sort(key=lambda f: (-f.severity.rank, f.type.value, f.table or ""))
    for n, f in enumerate(findings, start=1):
        f.id = f"{ctx.query_name}:F{n}"
    return findings, compute_scaling(ctx.result)
