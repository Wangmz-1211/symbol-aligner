"""LLM recall: the single fallback step in the whole pipeline.

Invoked only when fuzzy scoring is confident enough to be plausible but not
confident enough to auto-apply (the recall band). The LLM does not invent a
replacement; it only *chooses* among the top-k legacy keys already found by
fuzzy matching, or rejects them all. Output is constrained to a tiny JSON
object to keep token usage minimal, and results are cached.
"""

from __future__ import annotations

import json
import re

from .llm import LLMClient
from .models import IdentifierCandidate, MatchResult, MatchSource

_PROMPT = """You are a code symbol mapping assistant.
Match the identifier to the single best legacy token. Engineers abbreviate by
dropping vowels and truncating words, e.g. "fndMkt" is "findMarket",
"lstRsk" is "listRisk", "rcvAst" is "receiveAsset". Use the fuzzy scores as a
hint, but trust your own judgement if a lower-scored token is a better fit.
Always pick the closest match; only return null if the identifier is completely
unrelated to every candidate.

Return ONLY a JSON object:
  {{"key": "<legacy_token value copied verbatim>", "confidence": <0.0-1.0>}}
or {{"key": null, "confidence": 0.0}} if truly no candidate fits.

Identifier: {text}
Type: {id_type}

Candidates:
{candidates}
"""

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


class LLMRecall:
    def __init__(self, client: LLMClient, mapping: dict[str, str], cache: bool = True):
        self.client = client
        self.mapping = mapping
        self._cache: dict[tuple, MatchResult] | None = {} if cache else None
        self.audit_log: list[dict] = []

    def __call__(
        self, candidate: IdentifierCandidate, top_k: list[tuple[str, float]]
    ) -> MatchResult:
        """Make the recaller satisfy the ``Recaller`` callable protocol."""
        return self.recall(candidate, top_k)

    def recall(
        self, candidate: IdentifierCandidate, top_k: list[tuple[str, dict]]
    ) -> MatchResult:
        keys = tuple(k for k, _ in top_k)
        cache_key = (candidate.text, candidate.context, keys)
        if self._cache is not None and cache_key in self._cache:
            cached = self._cache[cache_key]
            return MatchResult(candidate, cached.matched_key, cached.replacement,
                               cached.confidence, cached.source, cached.reason)

        prompt = self._build_prompt(candidate, top_k)
        raw = ""
        try:
            raw = self.client.complete(prompt)
            result = self._parse(candidate, raw, keys)
        except Exception as exc:  # network/LLM failure must not abort the run
            result = MatchResult(candidate, None, None, 0.0, MatchSource.NONE,
                                 f"LLM recall failed: {exc}")

        self.audit_log.append({
            "identifier": candidate.text,
            "id_type": candidate.id_type.value,
            "candidates": [{"legacy_token": k, "scores": d} for k, d in top_k],
            "prompt": prompt,
            "raw_response": raw,
            "matched_key": result.matched_key,
            "confidence": result.confidence,
            "reason": result.reason,
        })

        if self._cache is not None:
            self._cache[cache_key] = result
        return result

    def _build_prompt(self, candidate: IdentifierCandidate, top_k: list[tuple[str, dict]]) -> str:
        entries = [{"legacy_token": k, "scores": detail} for k, detail in top_k]
        return _PROMPT.format(
            text=candidate.text,
            id_type=candidate.id_type.value,
            candidates=json.dumps(entries, ensure_ascii=False),
        )

    def _parse(
        self, candidate: IdentifierCandidate, raw: str, keys: tuple[str, ...]
    ) -> MatchResult:
        m = _JSON_RE.search(raw)
        if not m:
            return MatchResult(candidate, None, None, 0.0, MatchSource.NONE,
                               "LLM returned no JSON")
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            return MatchResult(candidate, None, None, 0.0, MatchSource.NONE,
                               "LLM returned invalid JSON")

        key = obj.get("key")
        confidence = float(obj.get("confidence", 0.0) or 0.0)
        if key is None:
            return MatchResult(candidate, None, None, 0.0, MatchSource.NONE,
                               "LLM rejected all candidates")

        legacy = self._resolve(key, keys)
        if legacy is None:
            # The LLM must stay within the offered candidates; anything else is
            # treated as a rejection to preserve auditability.
            return MatchResult(candidate, None, None, 0.0, MatchSource.NONE,
                               f"LLM picked out-of-set key {key!r}")
        return MatchResult(
            candidate, legacy, self.mapping[legacy], confidence, MatchSource.LLM,
            reason=f"LLM selected {legacy!r} from top-{len(keys)} (conf {confidence:.2f})",
        )

    def _resolve(self, key: str, keys: tuple[str, ...]) -> str | None:
        """Map the LLM's answer back to an offered legacy key.

        Models often echo the canonical (right-hand) side instead of the legacy
        key. Since the mapping is 1-to-1, either side unambiguously identifies an
        offered pair, so we accept both while still staying strictly in-set.
        """
        if key in keys:
            return key
        for k in keys:
            if self.mapping[k] == key:
                return k
        return None
