"""Deterministic recommendation rules.

Facts (findings) go in, proposed actions (recommendations) come out. Every
recommendation references the finding IDs that justify it.
"""

from __future__ import annotations

from dbscale.core.experiment import WorkloadResult
from dbscale.core.findings import Finding, FindingType, Severity
from dbscale.core.recommendations import Impact, Recommendation, RecommendationType
from dbscale.core.schema import Schema, Table

_MAX_IDENT = 63


def _q(ident: str) -> str:
    return '"' + ident.replace('"', '""') + '"'


def _qualified(table: Table) -> str:
    return f"{_q(table.schema_name)}.{_q(table.name)}" if table.schema_name else _q(table.name)


def index_name(table: str, columns: list[str]) -> str:
    name = f"idx_{table}_{'_'.join(columns)}"
    return name[:_MAX_IDENT]


def create_index_sql(table: Table, columns: list[str]) -> str:
    cols = ", ".join(_q(c) for c in columns)
    return f"CREATE INDEX CONCURRENTLY {_q(index_name(table.name, columns))} ON {_qualified(table)} ({cols});"


def existing_index_prefix(table: Table, columns: list[str]) -> str | None:
    """Name of an existing index whose leading columns match ``columns``."""
    for ix in table.indexes:
        if ix.has_expressions or ix.predicate:
            continue
        if ix.columns[: len(columns)] == columns:
            return ix.name
    return None


def _impact_from_selectivity(selectivity: float | None) -> Impact:
    if selectivity is None:
        return Impact.MEDIUM
    if selectivity < 0.01:
        return Impact.SIGNIFICANT
    if selectivity < 0.1:
        return Impact.HIGH
    if selectivity < 0.5:
        return Impact.MEDIUM
    return Impact.LOW


class RulesAdvisor:
    def __init__(self, schema: Schema):
        self.schema = schema

    def recommend(self, result: WorkloadResult) -> list[Recommendation]:
        findings = result.findings
        recs: list[Recommendation] = []
        index_recs: dict[tuple[str, tuple[str, ...]], Recommendation] = {}
        attach_later: list[Finding] = []

        sorts_by_table: dict[str, Finding] = {
            f.table: f for f in findings if f.type == FindingType.LARGE_SORT and f.table and f.columns
        }
        has_aggregation = any(f.type == FindingType.AGGREGATION_BOTTLENECK for f in findings)

        for f in findings:
            if f.type == FindingType.SEQUENTIAL_SCAN:
                # A full read feeding an aggregation or an ordered scan is addressed by those rules.
                if (
                    not f.columns
                    and not f.evidence.get("filter")
                    and (has_aggregation or f.table in sorts_by_table)
                ):
                    attach_later.append(f)
                    continue
                rec = self._sequential_scan(result, f, sorts_by_table)
            elif f.type == FindingType.LARGE_SORT:
                if has_aggregation and not f.table:
                    attach_later.append(f)  # sorting aggregate output: pre-aggregation removes it
                    continue
                rec = self._large_sort(result, f, findings)
            elif f.type == FindingType.EXPENSIVE_JOIN:
                rec = self._expensive_join(result, f)
            elif f.type == FindingType.AGGREGATION_BOTTLENECK:
                rec = self._aggregation(result, f)
            elif f.type == FindingType.EXECUTION_FAILURE:
                rec = self._investigate(
                    result,
                    f,
                    "Query does not complete at scale",
                    "The query failed or timed out, so no plan could be analyzed at that scale. "
                    "Run it manually with EXPLAIN (ANALYZE) at a smaller scale, or raise workload.timeout_ms.",
                )
            else:
                attach_later.append(f)
                continue
            if rec is None:
                attach_later.append(f)
                continue
            if rec.type in (RecommendationType.ADD_INDEX, RecommendationType.CHANGE_INDEX) and rec.table:
                key = (rec.table, tuple(rec.columns))
                if key in index_recs:
                    existing = index_recs[key]
                    existing.finding_ids = sorted(set(existing.finding_ids) | set(rec.finding_ids))
                    if rec.severity.rank > existing.severity.rank:
                        existing.severity = rec.severity
                    continue
                index_recs[key] = rec
            recs.append(rec)

        # Findings without a direct action: attach them as supporting evidence, or investigate.
        for f in attach_later:
            if recs:
                for rec in recs:
                    if f.id not in rec.finding_ids:
                        rec.finding_ids.append(f.id)
                    if f.severity.rank > rec.severity.rank and f.type in (
                        FindingType.NON_LINEAR_SCALING,
                        FindingType.THRESHOLD_BREACH,
                    ):
                        rec.severity = f.severity
            else:
                rec = self._fallback_investigate(result, f)
                if rec is not None:
                    recs.append(rec)

        recs.sort(key=lambda r: (-r.severity.rank, r.type.value))
        for n, rec in enumerate(recs, start=1):
            rec.id = f"{result.query.name}:R{n}"
        return recs

    # ------------------------------------------------------------------ rules

    def _sequential_scan(
        self, result: WorkloadResult, f: Finding, sorts_by_table: dict[str, Finding]
    ) -> Recommendation | None:
        table = self.schema.table(f.table or "")
        if table is None:
            return None
        ev = f.evidence
        if not f.columns:
            if ev.get("filter"):
                return self._investigate(
                    result,
                    f,
                    f"Filter on {table.name} could not be mapped to indexable columns",
                    f"The filter `{ev.get('filter')}` scans {ev.get('rows_scanned', 0):,} rows but references no "
                    "plain column of the table (expression or function). Consider an expression index or rewriting the predicate.",
                    rec_type=RecommendationType.REWRITE_QUERY,
                    confidence=0.5,
                )
            return Recommendation(
                id="",
                type=RecommendationType.REWRITE_QUERY,
                severity=f.severity,
                title=f"Avoid reading all of {table.name}",
                explanation=(
                    f"The query reads every row of '{table.name}' ({ev.get('rows_scanned', 0):,} at {f.scale_label}) "
                    "with no filter. No index can help; cost will keep growing with the table."
                ),
                proposed_action=(
                    "Add a selective predicate (e.g. a time window), paginate with a keyset, or maintain a "
                    "pre-aggregated summary table if the query computes totals."
                ),
                evidence=ev,
                confidence=0.5,
                expected_impact=Impact.HIGH,
                query_name=result.query.name,
                table=table.name,
                finding_ids=[f.id],
            )

        columns = list(f.columns)
        sort = sorts_by_table.get(table.name)
        order_by = list(sort.columns) if sort is not None else list(ev.get("order_by_columns") or [])
        for c in order_by:
            if c not in columns:
                columns.append(c)
        finding_ids = [f.id] + ([sort.id] if sort else [])
        covering = existing_index_prefix(table, columns[:1])
        if covering:
            return Recommendation(
                id="",
                type=RecommendationType.INVESTIGATE,
                severity=f.severity,
                title=f"Index '{covering}' exists on {table.name} but was not used",
                explanation=(
                    f"'{table.name}' already has index '{covering}' starting with {columns[0]}, yet the planner chose "
                    f"a sequential scan over {ev.get('rows_scanned', 0):,} rows. Typical causes: the predicate is not "
                    "selective enough, a type cast or function prevents index use, or statistics are stale."
                ),
                proposed_action=(
                    f"Run EXPLAIN (ANALYZE) on the query; check that `{ev.get('filter')}` compares {columns[0]} "
                    "without casts/functions and that the predicate is selective. Consider a composite or partial index."
                ),
                evidence={**ev, "existing_index": covering},
                confidence=0.6,
                expected_impact=Impact.MEDIUM,
                query_name=result.query.name,
                table=table.name,
                columns=columns,
                finding_ids=finding_ids,
            )
        selectivity = ev.get("selectivity")
        return Recommendation(
            id="",
            type=RecommendationType.ADD_INDEX,
            severity=f.severity,
            title=f"Add index on {table.name} ({', '.join(columns)})",
            explanation=(
                f"The query filters '{table.name}' on {', '.join(f.columns)} but no index covers it, so every row is "
                f"read ({ev.get('rows_scanned', 0):,} scanned → {ev.get('rows_output', 0):,} kept at {f.scale_label})."
                + (
                    f" Including {', '.join(order_by)} lets the index also satisfy the ORDER BY."
                    if order_by
                    else ""
                )
            ),
            proposed_action=create_index_sql(table, columns),
            evidence=ev,
            confidence=0.85 if len(f.columns) == 1 else 0.7,
            expected_impact=_impact_from_selectivity(selectivity),
            query_name=result.query.name,
            table=table.name,
            columns=columns,
            finding_ids=finding_ids,
            tradeoffs="Each index slows inserts/updates on the table and uses disk; verify column order matches the predicate.",
        )

    def _large_sort(
        self, result: WorkloadResult, f: Finding, findings: list[Finding]
    ) -> Recommendation | None:
        # If a sequential scan on the same table exists, the index recommendation already covers the sort keys.
        if any(x.type == FindingType.SEQUENTIAL_SCAN and x.table == f.table and x.columns for x in findings):
            return None
        table = self.schema.table(f.table or "")
        ev = f.evidence
        on_disk = (ev.get("space_type") or "").lower() == "disk"
        if table is not None and f.columns:
            covering = existing_index_prefix(table, f.columns)
            if covering is None:
                return Recommendation(
                    id="",
                    type=RecommendationType.ADD_INDEX,
                    severity=f.severity,
                    title=f"Add ordering index on {table.name} ({', '.join(f.columns)})",
                    explanation=(
                        f"{ev.get('rows_sorted', 0):,} rows are sorted by {', '.join(ev.get('sort_keys', []))} "
                        f"{'on disk' if on_disk else 'in memory'} at {f.scale_label}. An index in sort order lets the "
                        "database read rows already ordered and stop early when there is a LIMIT."
                    ),
                    proposed_action=create_index_sql(table, f.columns),
                    evidence=ev,
                    confidence=0.6,
                    expected_impact=Impact.HIGH if on_disk else Impact.MEDIUM,
                    query_name=result.query.name,
                    table=table.name,
                    columns=f.columns,
                    finding_ids=[f.id],
                    tradeoffs="Only helps if the planner can use the index order (matching ORDER BY direction and leading filter).",
                )
        return Recommendation(
            id="",
            type=RecommendationType.REWRITE_QUERY,
            severity=f.severity,
            title="Reduce the amount of data sorted",
            explanation=(
                f"{ev.get('rows_sorted', 0):,} rows are sorted by {', '.join(ev.get('sort_keys', []))} "
                f"{'on disk' if on_disk else 'in memory'} at {f.scale_label}, far more than the {ev.get('rows_returned') or 0:,} rows returned."
            ),
            proposed_action=(
                "Filter before sorting, sort on an indexed column, or use keyset pagination instead of OFFSET."
                + (
                    " If the sort must stay, raise work_mem for this session to keep it in memory."
                    if on_disk
                    else ""
                )
            ),
            evidence=ev,
            confidence=0.5,
            expected_impact=Impact.MEDIUM,
            query_name=result.query.name,
            table=f.table,
            columns=f.columns,
            finding_ids=[f.id],
        )

    def _expensive_join(self, result: WorkloadResult, f: Finding) -> Recommendation | None:
        ev = f.evidence
        table = self.schema.table(f.table or "")
        if (
            ev.get("join_kind") == "nested_loop"
            and ev.get("inner_scan") == "seq_scan"
            and table
            and f.columns
        ):
            if existing_index_prefix(table, f.columns) is None:
                return Recommendation(
                    id="",
                    type=RecommendationType.ADD_INDEX,
                    severity=f.severity,
                    title=f"Add index on {table.name} ({', '.join(f.columns)}) for the join",
                    explanation=(
                        f"The join re-scans '{table.name}' sequentially {ev.get('inner_loops', 0):,} times "
                        f"({ev.get('inner_rows', 0):,} rows read) because there is no index on the join key."
                    ),
                    proposed_action=create_index_sql(table, f.columns),
                    evidence=ev,
                    confidence=0.85,
                    expected_impact=Impact.SIGNIFICANT,
                    query_name=result.query.name,
                    table=table.name,
                    columns=f.columns,
                    finding_ids=[f.id],
                )
        if ev.get("hash_batches") and ev["hash_batches"] > 1:
            return self._investigate(
                result,
                f,
                "Hash join spills to disk",
                f"The hash table built on '{f.table or 'the inner relation'}' ({ev.get('inner_rows', 0):,} rows) exceeded "
                f"work_mem and was processed in {ev['hash_batches']} batches. Reduce the rows entering the join "
                "(filter earlier, select fewer columns) or increase work_mem for this workload.",
                confidence=0.6,
                impact=Impact.MEDIUM,
            )
        return self._investigate(
            result,
            f,
            "Join produces or reads a very large number of rows",
            f"The {ev.get('join_kind', 'join').replace('_', ' ')} handles {ev.get('rows_out', 0):,} output rows "
            f"from {ev.get('outer_rows', 0):,} × {ev.get('inner_rows', 0):,} inputs. Check whether the join can be "
            "restricted earlier or replaced by a pre-joined summary.",
            confidence=0.5,
        )

    def _aggregation(self, result: WorkloadResult, f: Finding) -> Recommendation:
        ev = f.evidence
        keys = ev.get("group_keys") or []
        return Recommendation(
            id="",
            type=RecommendationType.PRE_AGGREGATE,
            severity=f.severity,
            title="Pre-aggregate instead of recomputing on every query",
            explanation=(
                f"The query aggregates {ev.get('input_rows', 0):,} rows"
                + (f" grouped by {', '.join(keys)}" if keys else "")
                + f" at {f.scale_label}"
                + (f", spilling {ev.get('disk_kb'):,} kB to disk" if ev.get("disk_kb") else "")
                + ". This work is repeated on every execution and grows with the data."
            ),
            proposed_action=(
                "Maintain a summary table or materialized view"
                + (f" keyed by ({', '.join(keys)})" if keys else "")
                + " refreshed incrementally or on a schedule, and query that instead."
            ),
            evidence=ev,
            confidence=0.6,
            expected_impact=Impact.HIGH,
            query_name=result.query.name,
            table=f.table,
            columns=f.columns,
            finding_ids=[f.id],
            tradeoffs="Introduces staleness and write-side maintenance cost.",
        )

    def _investigate(
        self,
        result: WorkloadResult,
        f: Finding,
        title: str,
        explanation: str,
        *,
        rec_type: RecommendationType = RecommendationType.INVESTIGATE,
        confidence: float = 0.5,
        impact: Impact = Impact.UNKNOWN,
    ) -> Recommendation:
        return Recommendation(
            id="",
            type=rec_type,
            severity=f.severity,
            title=title,
            explanation=explanation,
            proposed_action="Review the execution plan in the results and validate the hypothesis in the sandbox.",
            evidence=f.evidence,
            confidence=confidence,
            expected_impact=impact,
            query_name=result.query.name,
            table=f.table,
            columns=f.columns,
            finding_ids=[f.id],
        )

    def _fallback_investigate(self, result: WorkloadResult, f: Finding) -> Recommendation | None:
        if f.type in (FindingType.NON_LINEAR_SCALING, FindingType.GROWING_LATENCY):
            return self._investigate(
                result,
                f,
                "Latency grows with data but no single bottleneck was identified",
                f"{f.description} Review the execution plan at the largest scale for the node whose time grows the most.",
                confidence=0.4,
            )
        if f.type == FindingType.THRESHOLD_BREACH:
            return self._investigate(
                result,
                f,
                "Latency exceeds the configured threshold",
                f"{f.description} The plan shows no sequential scan, large sort or expensive join; the query may simply "
                "return or process a lot of data. Consider tightening predicates or caching.",
                confidence=0.4,
            )
        if f.type == FindingType.EXCESSIVE_ROWS_SCANNED:
            return self._investigate(
                result,
                f,
                "Query discards most of the rows it reads",
                f"{f.description} Check whether a more selective index or predicate could avoid reading the discarded rows.",
                confidence=0.5,
            )
        if f.severity.rank >= Severity.MEDIUM.rank:
            return self._investigate(result, f, f.title, f.description, confidence=0.4)
        return None
