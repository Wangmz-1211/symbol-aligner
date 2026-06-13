"""Load and validate ``config.toml``.

Every threshold and scoring weight the pipeline uses comes from here; nothing is
hard-coded in the algorithm modules.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.toml"


@dataclass(frozen=True)
class ScoringWeights:
    ratio: float = 0.40
    partial_ratio: float = 0.25
    token_sort_ratio: float = 0.20
    jaro_winkler: float = 0.15

    def __post_init__(self) -> None:
        total = self.ratio + self.partial_ratio + self.token_sort_ratio + self.jaro_winkler
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"scoring weights must sum to 1.0, got {total}")


@dataclass(frozen=True)
class Thresholds:
    auto_apply: float = 0.99
    recall_min: float = 0.45

    def __post_init__(self) -> None:
        if not 0.0 <= self.recall_min <= self.auto_apply <= 1.0:
            raise ValueError(
                "thresholds must satisfy 0 <= recall_min <= auto_apply <= 1, "
                f"got recall_min={self.recall_min}, auto_apply={self.auto_apply}"
            )


@dataclass(frozen=True)
class LLMConfig:
    backend: str = "ollama"
    base_url: str = "http://localhost:11434"
    model: str = "llama3.1:8b"
    max_tokens: int = 64
    timeout: float = 30.0
    cache: bool = True


@dataclass(frozen=True)
class Config:
    top_k: int = 3
    thresholds: Thresholds = field(default_factory=Thresholds)
    weights: ScoringWeights = field(default_factory=ScoringWeights)
    llm: LLMConfig = field(default_factory=LLMConfig)


def load_config(path: str | Path | None = None) -> Config:
    """Read ``config.toml`` into a :class:`Config`. Missing keys fall back to defaults."""
    path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    matching = raw.get("matching", {})
    top_k = int(matching.get("top_k", 3))
    if top_k < 1:
        raise ValueError(f"matching.top_k must be >= 1, got {top_k}")

    return Config(
        top_k=top_k,
        thresholds=Thresholds(**raw.get("thresholds", {})),
        weights=ScoringWeights(**raw.get("scoring", {}).get("weights", {})),
        llm=LLMConfig(**raw.get("llm", {})),
    )
