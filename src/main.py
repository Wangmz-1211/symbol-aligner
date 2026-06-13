"""Pipeline orchestration: analyze -> match -> decide -> apply -> report.

The decision logic is intentionally simple and driven entirely by the two
thresholds in ``config.toml``:

    top-1 score >= auto_apply  -> apply the fuzzy match (no LLM)
    top-1 score >= recall_min  -> hand the top-k to the LLM recaller (if any)
    otherwise                  -> discard

There is no human-review tier. When no recaller is supplied (e.g. the
LLM-free pipeline of phase 4), candidates in the recall band are simply
discarded.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol

from .ast_analyze import ASTAnalyzer, detect_language
from .config import Config, load_config
from .fuzz_match import get_top_k
from .mapping import load_mapping
from .models import (
    AlignmentReport,
    IdentifierCandidate,
    IdentifierType,
    MatchResult,
    MatchSource,
)

# A "simple split" of a string literal into word-like tokens. Only these spans
# are considered for replacement; surrounding punctuation/whitespace is left
# untouched so the rest of the literal is preserved verbatim.
_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def string_subcandidates(candidate: IdentifierCandidate) -> list[IdentifierCandidate]:
    """Split a STRING candidate into per-token sub-candidates with exact spans.

    Byte offsets are computed from the UTF-8 encoding of the text prefix so
    multibyte content stays correct.
    """
    subs: list[IdentifierCandidate] = []
    text = candidate.text
    for m in _WORD_RE.finditer(text):
        token = m.group(0)
        byte_off = len(text[: m.start()].encode("utf-8"))
        token_bytes = len(token.encode("utf-8"))
        subs.append(
            IdentifierCandidate(
                text=token,
                id_type=IdentifierType.STRING,
                file_path=candidate.file_path,
                line=candidate.line,
                col_start=candidate.col_start + m.start(),
                col_end=candidate.col_start + m.end(),
                start_byte=candidate.start_byte + byte_off,
                end_byte=candidate.start_byte + byte_off + token_bytes,
                context=candidate.context,
                scope=candidate.scope,
            )
        )
    return subs


class Recaller(Protocol):
    """Resolves a recall-band candidate against its top-k fuzzy candidates."""

    def __call__(
        self, candidate: IdentifierCandidate, top_k: list[tuple[str, float]]
    ) -> MatchResult: ...


def _no_match(candidate: IdentifierCandidate, reason: str) -> MatchResult:
    return MatchResult(candidate, None, None, 0.0, MatchSource.NONE, reason)


def match_candidate(
    candidate: IdentifierCandidate,
    mapping: dict[str, str],
    config: Config,
    recaller: Recaller | None = None,
) -> MatchResult:
    """Match a single identifier and decide its fate via the thresholds."""
    top_k = get_top_k(candidate.text, mapping, config.weights, k=config.top_k)
    if not top_k:
        return _no_match(candidate, "empty mapping")

    best_key, best_score = top_k[0]
    th = config.thresholds

    if best_score >= th.auto_apply:
        source = MatchSource.EXACT if candidate.text == best_key else MatchSource.FUZZY
        return MatchResult(
            candidate, best_key, mapping[best_key], best_score, source,
            reason=f"fuzzy score {best_score:.3f} >= auto_apply {th.auto_apply}",
        )

    if best_score >= th.recall_min:
        if recaller is None:
            return _no_match(candidate, f"recall-band ({best_score:.3f}) but no recaller")
        return recaller(candidate, top_k)

    return _no_match(candidate, f"top score {best_score:.3f} < recall_min {th.recall_min}")


def align_source(
    source: bytes | str,
    mapping: dict[str, str],
    config: Config,
    language: str,
    file_path: str = "",
    recaller: Recaller | None = None,
) -> list[MatchResult]:
    """Run analysis + matching over in-memory source, returning all results."""
    candidates = ASTAnalyzer(language).extract(source, file_path)
    results: list[MatchResult] = []
    for c in candidates:
        if c.id_type is IdentifierType.STRING:
            # Match each word token inside the literal independently.
            results.extend(
                match_candidate(sub, mapping, config, recaller)
                for sub in string_subcandidates(c)
            )
        else:
            results.append(match_candidate(c, mapping, config, recaller))
    return results


def apply_changes(source: bytes, results: list[MatchResult]) -> bytes:
    """Splice in replacements by byte offset, applied right-to-left.

    Working from the end keeps earlier offsets valid as we mutate. Overlapping
    edits are not expected (AST spans are disjoint) but are skipped defensively.
    """
    edits = sorted(
        (r for r in results if r.applied),
        key=lambda r: r.candidate.start_byte,
        reverse=True,
    )
    out = source
    prev_start = len(source) + 1
    for r in edits:
        c = r.candidate
        if c.end_byte > prev_start:  # overlaps a later (already-applied) edit
            continue
        out = out[: c.start_byte] + r.replacement.encode("utf-8") + out[c.end_byte :]
        prev_start = c.start_byte
    return out


def build_recaller(mapping: dict[str, str], config: Config) -> Recaller:
    """Construct the configured LLM recaller. Imports are deferred so the
    LLM-free path never pulls in the HTTP/LLM stack."""
    from .llm import build_client
    from .recall import LLMRecall

    return LLMRecall(build_client(config.llm), mapping, cache=config.llm.cache)


def align_file(
    file_path: str,
    mapping_path: str,
    config_path: str | None = None,
    *,
    dry_run: bool = False,
    language: str | None = None,
    use_llm: bool = False,
    recaller: Recaller | None = None,
) -> AlignmentReport:
    """Align a single file. Writes changes in place unless ``dry_run``.

    When ``use_llm`` is set and no explicit ``recaller`` is given, an LLM
    recaller is built from the config to resolve recall-band candidates.
    """
    config = load_config(config_path)
    mapping = load_mapping(mapping_path)
    language = language or detect_language(file_path)
    if language is None:
        raise ValueError(f"could not detect language for {file_path!r}")

    if recaller is None and use_llm:
        recaller = build_recaller(mapping, config)

    source = Path(file_path).read_bytes()
    results = align_source(source, mapping, config, language, file_path, recaller)

    if not dry_run and any(r.applied for r in results):
        Path(file_path).write_bytes(apply_changes(source, results))

    return AlignmentReport(file_path=file_path, results=results, dry_run=dry_run)


def main() -> None:  # console-script entry point; full CLI is future work
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Symbol Aligner")
    parser.add_argument("file")
    parser.add_argument("mapping")
    parser.add_argument("--config", default=None)
    parser.add_argument("--apply", action="store_true", help="write changes (default: dry-run)")
    parser.add_argument("--use-llm", action="store_true", help="resolve recall-band matches via the LLM")
    args = parser.parse_args()

    report = align_file(
        args.file, args.mapping, args.config,
        dry_run=not args.apply, use_llm=args.use_llm,
    )
    print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
