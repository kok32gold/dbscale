# Adding an AI provider

The AI layer is optional and provider-neutral. Core experiments run with
`ai.enabled: false`.

```python
class MyProvider(LLMProvider):
    name = "myprovider"
    model = ""

    def complete(self, system: str, user: str) -> str:
        ...
```

Wire it in `create_provider` so `ai.provider: myprovider` selects it.

## Rules

- Input is two strings: the system prompt and a JSON payload of the result.
- Output is text, preferably the JSON object described in
  [../ai/advisor.md](../ai/advisor.md).
- Raise `LLMProviderError` on failure. `run_ai_analysis` catches it and stores
  `ai.error`. The experiment result is kept.
- Scrub secrets with `redact_secrets` before putting exception text on the result.
- Do not accept a connection string, and do not import an adapter.
- Tests use a fake `httpx` transport or a stub `LLMProvider`. CI must not call
  a hosted model.

Supported names today: `openai`, `anthropic`, `ollama`, `openai-compatible`,
`openrouter`, `lmstudio`, `vllm`. Ollama and any OpenAI-compatible local
server need no cloud account. Hosted providers run only when the user sets
`ai.enabled` or passes `--ai` / `dbscale explain`.

If the provider is down, `dbscale run` still prints findings and writes JSON.
The AI section shows the error.
