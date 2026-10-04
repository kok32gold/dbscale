# Adding an analyzer

Analyzers turn measurements into findings. They are pure: same
`AnalysisContext`, same findings.

```python
class MyAnalyzer(Analyzer):
    name = "my_analyzer"

    def analyze(self, ctx: AnalysisContext) -> list[Finding]:
        measurement = ctx.largest()
        if measurement is None or measurement.plan is None:
            return []
        if measurement.plan.rows_scanned is None:
            return []
        ...
```

## Rules

- Read `ctx.schema`, `ctx.result`, `ctx.thresholds`, `ctx.scale`.
- Return `Finding`s with `type`, `severity`, `title`, `description`, and
  `evidence`. Use `_finding(...)` so the query name is attached.
- Do not return recommendations. A rule in `advisors/rules/advisor.py` may
  turn your finding into a proposed action.
- Do not import `dbscale.adapters`, open a socket, or read the environment.
- If `rows_scanned` or another fact is `None`, skip. Do not treat it as zero.
- Add the instance to `DEFAULT_ANALYZERS` only if it should run for every
  experiment. Otherwise document passing it to `ExperimentRunner(analyzers=...)`.

## Tests

Build a `PlanNode` or load `tests/fixtures/plans/<name>.json` via
`helpers.load_plan`. Assert the finding type, severity, and the evidence
fields you claim. Add a case where the plan is missing and the analyzer
returns nothing.

A new analyzer does not require a change to the benchmark runner, the CLI,
or an AI provider.
