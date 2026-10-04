"""The experiment pipeline.

source DB ──inspect──▶ Schema ──plan──▶ GenerationPlan
                                          │
sandbox ◀──create schema / populate / index / analyze (per scale)
   │
   └──▶ measurements ──analyze──▶ findings ──rules──▶ recommendations ──(optional)──▶ LLM
"""

from __future__ import annotations

import time
from dataclasses import asdict
from datetime import UTC, datetime

from dbscale import __version__
from dbscale.adapters import DatabaseAdapter, get_adapter
from dbscale.advisors.llm import LLMAdvisor, LLMProvider, LLMProviderError, create_provider
from dbscale.advisors.rules import RulesAdvisor
from dbscale.analysis import AnalysisContext, Analyzer, analyze_workload
from dbscale.benchmark import BenchmarkRunner
from dbscale.core.config import ConfigError, ExperimentConfig
from dbscale.core.experiment import AIAnalysis, DatabaseInfo, ExperimentResult, WorkloadResult
from dbscale.core.scale import ScalePlan, format_count, resolve_scale
from dbscale.core.schema import Schema
from dbscale.experiments.progress import NullProgress, ProgressListener
from dbscale.generation import GenerationPlan, plan_generation
from dbscale.infrastructure import Sandbox, create_sandbox
from dbscale.redact import redact_secrets


class ExperimentError(RuntimeError):
    pass


class ExperimentRunner:
    def __init__(
        self,
        config: ExperimentConfig,
        listener: ProgressListener | None = None,
        *,
        analyzers: list[Analyzer] | None = None,
        llm_provider: LLMProvider | None = None,
        sandbox: Sandbox | None = None,
    ):
        self.config = config
        self.listener: ProgressListener = listener or NullProgress()
        self.analyzers = analyzers
        self.llm_provider = llm_provider
        self._sandbox_override = sandbox
        self._printed_notes: set[str] = set()

    # ------------------------------------------------------------ discovery

    def inspect(self) -> tuple[Schema, DatabaseInfo]:
        """Connect read-only to the source, inspect it, and resolve nothing else."""
        adapter_cls = get_adapter(self.config.database.type)
        self.listener.start("Connecting to source database")
        with adapter_cls.connect(self.config.database.connection, read_only=True) as source:
            info = source.server_info()
            self.listener.done(f"Connected to {_pretty_type(info.type)} {info.version} (read-only)")
            self.listener.start("Inspecting schema")
            schema = source.inspect_schema(
                self.config.database.schemas,
                self.config.database.include_tables,
                self.config.database.exclude_tables,
                sample_common_values=self.config.database.sample_common_values,
            )
            if self.config.database.sample_common_values:
                self.listener.note(
                    "sample_common_values is on: most common values of low-cardinality columns were read"
                )
        if not schema.tables:
            raise ExperimentError(
                "No tables found. Check database.schemas / include_tables, and that the user can read the catalog."
            )
        self.listener.done(
            f"Inspected schema: {len(schema.tables)} tables, ~{format_count(schema.total_rows)} rows"
        )
        caps = getattr(adapter_cls, "capabilities", None)
        db_info = DatabaseInfo(
            type=info.type,
            version=info.version,
            source_tables=len(schema.tables),
            source_rows=schema.total_rows,
            capabilities=asdict(caps) if caps is not None else {},
        )
        return schema, db_info

    # ------------------------------------------------------------------ run

    def run(self) -> ExperimentResult:
        cfg = self.config
        started = datetime.now(UTC)
        try:
            workload = cfg.resolved_workload()
        except (ValueError, OSError) as exc:
            raise ConfigError(str(exc)) from exc
        schema, db_info = self.inspect()
        scale_plan = resolve_scale(cfg.scale.parsed_targets(), schema, cfg.scale.base_rows)

        sandbox = self._sandbox_override or create_sandbox(cfg.sandbox)
        self.listener.start(f"Creating isolated database ({sandbox.description})")
        url = sandbox.start()
        db_info.sandbox = sandbox.description
        adapter_cls = get_adapter(cfg.database.type)
        sandbox_db: DatabaseAdapter | None = None
        workloads: list[WorkloadResult] = [WorkloadResult(query=q) for q in workload.queries]
        status = "completed"
        gen_plan: GenerationPlan | None = None
        try:
            sandbox_db = adapter_cls.connect(url)
            self.listener.done("Created isolated database")
            # Compatibility depends on the sandbox image, so plan generation runs after it.
            fidelity = sandbox_db.adapt_to_sandbox(schema)
            gen_plan = plan_generation(schema, seed=cfg.scale.seed)
            gen_plan.notes = [*fidelity, *gen_plan.notes]
            self._emit_notes(gen_plan.notes)
            self._reproduce_schema(sandbox_db, schema, gen_plan, sandbox)

            bench = BenchmarkRunner(sandbox_db, workload, include_raw_plans=cfg.output.include_raw_plans)
            for idx, target in enumerate(scale_plan.targets):
                self._populate(sandbox_db, schema, gen_plan, scale_plan, idx, target)
                self.listener.start(f"Testing {target.label} ({len(workload.queries)} queries)")
                measurements = bench.run_scale(
                    target,
                    on_query=lambda q, m: self.listener.progress(f"{q.name}: {_fmt_latency(m)}"),
                )
                for wr, m in zip(workloads, measurements, strict=True):
                    wr.measurements.append(m)
                failures = sum(1 for m in measurements if m.error)
                suffix = f", {failures} failed" if failures else ""
                self.listener.done(f"Tested {target.label} (~{format_count(target.total_rows)} rows{suffix})")
        except Exception:
            status = "failed"
            raise
        else:
            status = _experiment_status(workloads)
        finally:
            if sandbox_db is not None and gen_plan is not None:
                self._emit_notes([*gen_plan.notes, *sandbox_db.fidelity_notes()])
                for note in sandbox_db.fidelity_notes():
                    if note not in gen_plan.notes:
                        gen_plan.notes.append(note)
            if sandbox_db is not None:
                if not cfg.sandbox.keep and cfg.sandbox.type == "url":
                    try:
                        sandbox_db.drop_schema(schema)
                    except Exception:  # noqa: BLE001
                        pass
                sandbox_db.close()
            if sandbox.keep or cfg.sandbox.keep:
                kept = redact_secrets(url)
                name = getattr(sandbox, "name", None)
                if name:
                    kept = f"{kept} (container {name})"
                self.listener.note(f"Sandbox kept: {kept}")
            else:
                sandbox.destroy()

        if gen_plan is None:
            raise ExperimentError("Sandbox closed before a generation plan was built")

        self.listener.start("Analyzing results")
        findings_total = self._analyze(schema, workloads, scale_plan)
        self.listener.done(f"Analyzed results: {findings_total} findings")

        result = ExperimentResult(
            dbscale_version=__version__,
            name=cfg.name,
            started_at=started,
            finished_at=datetime.now(UTC),
            status=status,
            database=db_info,
            schema=schema,
            scale=scale_plan,
            thresholds=cfg.thresholds,
            workloads=workloads,
            findings=[f for w in workloads for f in w.findings],
            recommendations=[r for w in workloads for r in w.recommendations],
            metadata={
                "generation_notes": gen_plan.notes,
                "workload": {
                    "runs": workload.runs,
                    "warmup": workload.warmup,
                    "timeout_ms": workload.timeout_ms,
                },
            },
        )

        if cfg.ai.enabled:
            result.ai = run_ai_analysis(result, cfg, self.listener, provider=self.llm_provider)
        return result

    def _emit_notes(self, notes: list[str]) -> None:
        fresh = [note for note in notes if note not in self._printed_notes]
        for note in fresh[:25]:
            self.listener.note(note)
        if len(fresh) > 25:
            self.listener.note(f"{len(fresh) - 25} more compatibility notes saved in the result")
        self._printed_notes.update(fresh)

    # --------------------------------------------------------------- steps

    def _reproduce_schema(
        self, db: DatabaseAdapter, schema: Schema, gen_plan: GenerationPlan, sandbox: Sandbox
    ) -> None:
        self.listener.start("Reproducing schema in sandbox")
        if self.config.sandbox.type == "url":
            db.drop_schema(schema)
        db.create_schema(schema, unlogged=self.config.sandbox.unlogged_tables)
        self.listener.done(f"Reproduced schema ({len(schema.tables)} tables)")

    def _populate(
        self,
        db: DatabaseAdapter,
        schema: Schema,
        gen_plan: GenerationPlan,
        scale_plan: ScalePlan,
        idx: int,
        target,  # ResolvedScale
    ) -> None:
        self.listener.start(
            f"Generating synthetic data at {target.label} (~{format_count(target.total_rows)} rows)"
        )
        t0 = time.perf_counter()
        if idx > 0:
            # Indexes and foreign keys are removed only so the next load is not
            # maintained row by row, then both are created again below. A foreign
            # key can depend on a non-primary unique index, so the key goes first.
            if self.config.sandbox.foreign_keys:
                db.drop_foreign_keys(schema)
            db.drop_indexes(schema)
            db.truncate(schema)

        def progress(table: str, done: int, total: int) -> None:
            self.listener.progress(f"{table}: {format_count(done)}/{format_count(total)} rows")

        db.populate(schema, gen_plan, target, progress=progress)
        self.listener.progress("building indexes")
        db.create_indexes(schema, gen_plan)
        if self.config.sandbox.foreign_keys:
            db.create_foreign_keys(schema)
        self.listener.progress("analyzing tables")
        db.analyze(schema)
        elapsed = time.perf_counter() - t0
        self.listener.done(
            f"Generated synthetic data at {target.label} (~{format_count(target.total_rows)} rows in {elapsed:.0f}s)"
        )

    def _analyze(self, schema: Schema, workloads: list[WorkloadResult], scale_plan: ScalePlan) -> int:
        rules = RulesAdvisor(schema)
        total = 0
        for wr in workloads:
            ctx = AnalysisContext(
                schema=schema, result=wr, thresholds=self.config.thresholds, scale=scale_plan
            )
            wr.findings, wr.scaling = analyze_workload(ctx, self.analyzers)
            wr.recommendations = rules.recommend(wr)
            total += len(wr.findings)
        return total


def run_ai_analysis(
    result: ExperimentResult,
    config: ExperimentConfig,
    listener: ProgressListener | None = None,
    *,
    provider: LLMProvider | None = None,
):
    """Send structured results to the configured LLM. Never raises; errors land in ``AIAnalysis.error``."""
    listener = listener or NullProgress()
    try:
        provider = provider or create_provider(config.ai)
    except LLMProviderError as exc:
        listener.fail(f"AI analysis skipped: {exc}")
        return AIAnalysis(provider=config.ai.provider, model=config.ai.model, summary="", error=str(exc))
    listener.start(f"Asking {provider.name}/{provider.model} to interpret results")
    analysis = LLMAdvisor(provider, max_plan_nodes=config.ai.max_plan_nodes).advise(result)
    if analysis.error:
        listener.fail(f"AI analysis failed: {analysis.error}")
    else:
        listener.done(f"AI analysis complete ({len(analysis.recommendations)} recommendations)")
    return analysis


def _experiment_status(workloads: list[WorkloadResult]) -> str:
    """``completed`` when every query ran; ``partial`` when some failed; ``failed`` when all failed."""
    measurements = [m for w in workloads for m in w.measurements]
    if not measurements:
        return "completed"
    failed = sum(1 for m in measurements if m.error)
    if failed == 0:
        return "completed"
    if failed == len(measurements):
        return "failed"
    return "partial"


def _pretty_type(t: str) -> str:
    return {"postgres": "PostgreSQL"}.get(t, t)


def _fmt_latency(m) -> str:
    if m.error:
        return "failed"
    if m.latency is None:
        return "?"
    p50 = m.latency.p50_ms
    return f"{p50 / 1000:.2f}s" if p50 >= 1000 else f"{p50:.0f}ms"
