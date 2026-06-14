"""LLM client abstraction.

The recall step needs only a tiny, single-shot completion, so the interface is
deliberately minimal. The default backend talks to a local Ollama server; other
backends can implement the same :class:`LLMClient` protocol.
"""

from __future__ import annotations

import os
from typing import Protocol

import httpx

from .config import LLMConfig


class LLMClient(Protocol):
    def complete(self, prompt: str) -> str: ...


class OllamaClient:
    """Calls a local Ollama server's ``/api/generate`` endpoint (non-streaming)."""

    def __init__(self, config: LLMConfig):
        self.config = config

    def complete(self, prompt: str) -> str:
        payload = {
            "model": self.config.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "num_predict": self.config.max_tokens,
                "temperature": 0.0,  # deterministic selection
            },
        }
        resp = httpx.post(
            f"{self.config.base_url.rstrip('/')}/api/generate",
            json=payload,
            timeout=self.config.timeout,
        )
        resp.raise_for_status()
        return resp.json().get("response", "")


class AnthropicClient:
    """Calls the Anthropic Messages API. API key is read from CLAUDE_API_KEY env var."""

    def __init__(self, config: LLMConfig):
        import anthropic

        api_key = os.environ.get("CLAUDE_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("CLAUDE_API_KEY environment variable is not set")
        self._client = anthropic.Anthropic(api_key=api_key)
        self.config = config

    def complete(self, prompt: str) -> str:
        msg = self._client.messages.create(
            model=self.config.model,
            max_tokens=self.config.max_tokens,
            temperature=0.0,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text if msg.content else ""


class OpenAIClient:
    """Calls any OpenAI-compatible /v1/chat/completions endpoint.

    API key is read from AGNES_API_KEY, falling back to OPENAI_API_KEY.
    """

    def __init__(self, config: LLMConfig):
        api_key = os.environ.get("AGNES_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("AGNES_API_KEY or OPENAI_API_KEY environment variable is not set")
        self._api_key = api_key
        self.config = config

    def complete(self, prompt: str) -> str:
        payload = {
            "model": self.config.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": self.config.max_tokens,
            "temperature": 0.0,
        }
        resp = httpx.post(
            f"{self.config.base_url.rstrip('/')}/v1/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=self.config.timeout,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


def build_client(config: LLMConfig) -> LLMClient:
    """Instantiate the configured backend."""
    if config.backend == "ollama":
        return OllamaClient(config)
    if config.backend == "anthropic":
        return AnthropicClient(config)
    if config.backend == "openai":
        return OpenAIClient(config)
    raise ValueError(f"unsupported LLM backend: {config.backend!r}")
