"""Fuzzy matching: score a query identifier against the mapping's legacy keys.

A weighted blend of several RapidFuzz metrics covers the kinds of non-standard
naming seen in legacy code (typos, dropped vowels, abbreviations, reordering).
Candidates are matched whole; the caller is responsible for splitting string
literals into tokens before calling here.
"""

from __future__ import annotations

from rapidfuzz import fuzz
from rapidfuzz.distance import JaroWinkler

from .config import ScoringWeights


def score(query: str, key: str, weights: ScoringWeights) -> float:
    """Weighted similarity between ``query`` and a legacy ``key`` in ``[0, 1]``.

    An exact match short-circuits to 1.0 so identical strings always win.
    """
    if query == key:
        return 1.0
    return (
        weights.ratio * fuzz.ratio(query, key)
        + weights.partial_ratio * fuzz.partial_ratio(query, key)
        + weights.token_sort_ratio * fuzz.token_sort_ratio(query, key)
        + weights.jaro_winkler * (JaroWinkler.similarity(query, key) * 100.0)
    ) / 100.0


def get_top_k(
    query: str,
    mapping: dict[str, str],
    weights: ScoringWeights,
    k: int = 3,
) -> list[tuple[str, float]]:
    """Return up to ``k`` ``(legacy_key, score)`` pairs, highest score first.

    Ties are broken deterministically by the legacy key, so output is stable.
    """
    scored = [(key, score(query, key, weights)) for key in mapping]
    scored.sort(key=lambda kv: (-kv[1], kv[0]))
    return scored[:k]
