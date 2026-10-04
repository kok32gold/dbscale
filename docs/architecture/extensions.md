# Extension points

Each extension has one contract. You should not need to edit unrelated
packages to add one.

| Extension | Contract | Register by |
| --- | --- | --- |
| Database adapter | `DatabaseAdapter` in `adapters/base.py` | `register_adapter` in the adapter package |
| Data generator | Emit a `GenerationPlan` of `ValueSpec`s | Called from the experiment runner via `plan_generation` |
| Workload | `Workload` / `QuerySpec` (`kind: sql` today) | `dbscale.yaml` `workload.queries` |
| Analyzer | `Analyzer.analyze(AnalysisContext) -> list[Finding]` | `DEFAULT_ANALYZERS` or `ExperimentRunner(analyzers=...)` |
| Recommendation provider | Map findings to `Recommendation`s | `RulesAdvisor` today; pass a different advisor only if you are changing the runner |
| AI provider | `LLMProvider.complete(system, user) -> str` | `create_provider` in `advisors/llm/providers.py` |
| Output formatter | Read `ExperimentResult` | `reporting/`; JSON writers must keep `format_version` |
| Sandbox | `Sandbox.start() -> url`, `destroy()` | `create_sandbox` from `sandbox.type` |

## What stays stable

Pre-1.0, these are the surfaces contributors should treat as contracts:

- `DatabaseAdapter` methods and `AdapterCapabilities`
- `Schema`, `GenerationPlan`, `PlanNode` / `PlanSummary`, `Finding`, `Recommendation`
- `dbscale.yaml` keys documented in [experiments/configuration.md](../experiments/configuration.md)
- `dbscale-results.json` `format_version`
- CLI commands `init`, `inspect`, `run`, `report`, `explain`

Adding a field or an optional method is fine. Renaming or removing one is a
breaking change and needs a changelog entry. Result-format breaks also bump
`RESULT_FORMAT_VERSION`.

## What is not a hidden registry

Built-in adapters register on import of `dbscale.adapters` (the package import
loads `postgres`). A third-party adapter is registered by importing its module
before `get_adapter` runs. There is no entry-point scan and no network lookup.
If your adapter is not imported, `database.type` will say it is unknown. That
is intentional.

Guides:

- [Adding an adapter](../contributing/adapters.md)
- [Adding an analyzer](../contributing/analyzers.md)
- [Adding an AI provider](../contributing/ai-providers.md)
