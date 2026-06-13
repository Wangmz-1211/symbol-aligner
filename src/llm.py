"""LLM client abstraction.

The recall step needs only a tiny, single-shot completion, so the interface is
deliberately minimal. The default backend talks to a local Ollama server; other
backends can implement the same :class:`LLMClient` protocol.
"""

from __future__ import annotations

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


def build_client(config: LLMConfig) -> LLMClient:
    """Instantiate the configured backend."""
    if config.backend == "ollama":
        return OllamaClient(config)
    raise ValueError(f"unsupported LLM backend: {config.backend!r}")
