"""LLM provider abstraction. Providers only turn (system, user) text into text."""

from __future__ import annotations

from abc import ABC, abstractmethod

import httpx

from dbscale.core.config import AIConfig
from dbscale.redact import redact_secrets


class LLMProviderError(RuntimeError):
    pass


class LLMProvider(ABC):
    name: str = "llm"
    model: str = ""

    @abstractmethod
    def complete(self, system: str, user: str) -> str: ...


class OpenAICompatibleProvider(LLMProvider):
    """OpenAI Chat Completions API. Also works for Ollama, LM Studio, vLLM, OpenRouter, etc."""

    name = "openai"

    def __init__(
        self, model: str, api_key: str | None, base_url: str | None = None, timeout_s: float = 120.0
    ):
        self.model = model
        self.api_key = api_key
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        self.timeout_s = timeout_s

    def complete(self, system: str, user: str) -> str:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        body = {
            "model": self.model,
            "temperature": 0.2,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        try:
            resp = httpx.post(
                f"{self.base_url}/chat/completions", json=body, headers=headers, timeout=self.timeout_s
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            raise LLMProviderError(f"{self.name} request failed: {redact_secrets(str(exc))}") from exc


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(
        self, model: str, api_key: str | None, base_url: str | None = None, timeout_s: float = 120.0
    ):
        if not api_key:
            raise LLMProviderError("ai.api_key is required for the anthropic provider")
        self.model = model
        self.api_key = api_key
        self.base_url = (base_url or "https://api.anthropic.com").rstrip("/")
        self.timeout_s = timeout_s

    def complete(self, system: str, user: str) -> str:
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }
        body = {
            "model": self.model,
            "max_tokens": 4000,
            "temperature": 0.2,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        try:
            resp = httpx.post(
                f"{self.base_url}/v1/messages", json=body, headers=headers, timeout=self.timeout_s
            )
            resp.raise_for_status()
            data = resp.json()
            return "".join(block.get("text", "") for block in data["content"] if block.get("type") == "text")
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise LLMProviderError(f"{self.name} request failed: {redact_secrets(str(exc))}") from exc


def create_provider(config: AIConfig) -> LLMProvider:
    provider = config.provider.lower()
    if provider in ("openai", "openai-compatible", "ollama", "openrouter", "lmstudio", "vllm"):
        base_url = config.base_url
        if provider == "ollama" and not base_url:
            base_url = "http://localhost:11434/v1"
        p = OpenAICompatibleProvider(config.model, config.api_key, base_url, config.timeout_s)
        p.name = provider
        return p
    if provider == "anthropic":
        return AnthropicProvider(config.model, config.api_key, config.base_url, config.timeout_s)
    raise LLMProviderError(
        f"Unknown ai.provider '{config.provider}'. Use openai, anthropic, ollama or openai-compatible"
    )
