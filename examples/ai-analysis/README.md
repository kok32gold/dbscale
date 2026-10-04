# AI analysis

The benchmark does not need a model. This example runs one query with AI
disabled, writes `dbscale-results.json`, and shows how to interpret that file
afterwards.

```bash
docker compose -f ../postgres/docker-compose.yml up -d --wait
dbscale run
```

`ai.enabled` is false. The report contains measurements, findings, and
rule-based recommendations only.

## Interpret a saved result

This step needs a provider you choose. It does not reopen the database.

```bash
# local, no cloud account, if Ollama is running:
dbscale explain dbscale-results.json --provider ollama --model llama3.1

# hosted, only if you set a key yourself:
# OPENAI_API_KEY=... dbscale explain dbscale-results.json --provider openai --model gpt-4o-mini
```

## Provider unavailable

```bash
dbscale explain dbscale-results.json --provider openai --model gpt-4o-mini
```

With no API key, or with the provider down, the command records `ai.error`
and leaves the measured result in place. `dbscale report dbscale-results.json`
still prints the findings.

DBScale never sends the result anywhere unless you run `explain` or
`dbscale run --ai`.
