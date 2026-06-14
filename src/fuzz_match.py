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


def score_detail(query: str, key: str, weights: ScoringWeights) -> dict:
    """Return all component scores and the final weighted score for (query, key).

    All values are in [0, 1].  Exact matches short-circuit to 1.0 everywhere.
    """
    if query == key:
        return {"ratio": 1.0, "partial_ratio": 1.0,
                "token_sort_ratio": 1.0, "jaro_winkler": 1.0, "weighted": 1.0}
    ratio      = fuzz.ratio(query, key) / 100.0
    partial    = fuzz.partial_ratio(query, key) / 100.0
    token_sort = fuzz.token_sort_ratio(query, key) / 100.0
    jw         = JaroWinkler.similarity(query, key)
    weighted   = (
        weights.ratio          * ratio
        + weights.partial_ratio    * partial
        + weights.token_sort_ratio * token_sort
        + weights.jaro_winkler     * jw
    )
    return {
        "ratio":            round(ratio, 4),
        "partial_ratio":    round(partial, 4),
        "token_sort_ratio": round(token_sort, 4),
        "jaro_winkler":     round(jw, 4),
        "weighted":         round(weighted, 4),
    }


def score(query: str, key: str, weights: ScoringWeights) -> float:
    """Final weighted similarity in [0, 1]. Kept for external callers."""
    return score_detail(query, key, weights)["weighted"]


def get_top_k(
    query: str,
    mapping: dict[str, str],
    weights: ScoringWeights,
    k: int = 3,
) -> list[tuple[str, dict]]:
    """Return up to ``k`` ``(legacy_key, score_detail)`` pairs, best first.

    Ties are broken deterministically by the legacy key, so output is stable.
    """
    scored = [(key, score_detail(query, key, weights)) for key in mapping]
    scored.sort(key=lambda kv: (-kv[1]["weighted"], kv[0]))
    return scored[:k]
