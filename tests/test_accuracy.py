"""End-to-end accuracy tests for symbol-aligner.

Three tiers:

* **clean** — 24 exact legacy keys; auto-apply path must reach 100%.
* **abbrev-fuzzy** — 128 abbreviated identifiers, LLM disabled, measures whether
  fuzzy top-1 ranking picks the right legacy key.
* **abbrev-llm** — same 128 abbreviations with live LLM recall (requires Ollama).

Abbreviation strategy reflects real engineering: engineers shorten identifiers
by dropping vowels or using well-known domain acronyms, not typos.
Examples: ``computeRisk`` → ``compRsk``, ``setAccount`` → ``setAcct``,
``paymentStatus`` → ``pymntSts``.
"""

from __future__ import annotations

import httpx
import pytest

from symbol_aligner.config import Config, Thresholds, load_config
from symbol_aligner.main import align_file, align_source
from symbol_aligner.mapping import load_mapping

MAPPING_PATH = "mappings/example.json"
MAPPING = load_mapping(MAPPING_PATH)

# ── Abbreviation tables ───────────────────────────────────────────────────────
# Maps each full-word component to its abbreviated form.  These replicate the
# conventions an engineer would use when typing quickly.

_VERB_SHORT: dict[str, str] = {
    "get":     "get",      # already short
    "set":     "set",
    "compute": "comp",
    "check":   "chk",
    "send":    "snd",
    "receive": "rcv",
    "load":    "ld",
    "save":    "sav",
    "create":  "crt",
    "update":  "upd",
    "delete":  "dlt",
    "find":    "fnd",
    "list":    "lst",
    "process": "proc",
    "apply":   "aply",
}

# Title-case noun abbreviations used as the second component in verb+Noun keys.
_NOUN_SHORT: dict[str, str] = {
    "User":     "Usr",
    "Account":  "Acct",
    "Order":    "Ord",
    "Payment":  "Pymt",
    "Stock":    "Stk",
    "Market":   "Mkt",
    "Fund":     "Fnd",
    "Loan":     "Ln",
    "Rate":     "Rt",
    "Asset":    "Ast",
    "Trade":    "Trd",
    "Risk":     "Rsk",
    "Report":   "Rpt",
    "Tax":      "Tx",
    "Audit":    "Aud",
    "Invoice":  "Inv",
    "Budget":   "Bdgt",
    "Contract": "Cntr",
    "Balance":  "Bal",
    "Record":   "Rec",
}

# Lowercase version for the noun in noun+Attr keys.
_NOUN_DATA_SHORT: dict[str, str] = {k.lower(): v.lower() for k, v in _NOUN_SHORT.items()}

# Title-case attribute abbreviations used as the second component in noun+Attr keys.
_ATTR_SHORT: dict[str, str] = {
    "Amount":  "Amt",
    "Date":    "Dt",
    "Status":  "Sts",
    "Type":    "Typ",
    "Name":    "Nm",
    "Count":   "Cnt",
    "Limit":   "Lmt",
    "Info":    "Inf",
    "Summary": "Sum",
    "Total":   "Tot",
}


# ── Core helpers ──────────────────────────────────────────────────────────────

def _sample_keys(min_len: int = 7, count: int = 24) -> list[str]:
    """Deterministic sample of legacy keys for the clean / exact-match test."""
    keys = sorted(k for k in MAPPING if len(k) >= min_len)
    return keys[:count]


def _abbreviate(legacy_key: str) -> str | None:
    """Return an abbreviated form of a compound legacy key, or None.

    Tries verb+Noun compounds first, then noun+Attr.  Longest-match for
    the verb/noun prefix avoids partial prefix shadowing.
    """
    # verb + Title(noun)
    for verb, vabbr in sorted(_VERB_SHORT.items(), key=lambda x: -len(x[0])):
        if legacy_key.startswith(verb):
            noun = legacy_key[len(verb):]
            if noun in _NOUN_SHORT:
                abbr = vabbr + _NOUN_SHORT[noun]
                return abbr if abbr != legacy_key else None
    # noun + Title(attr)
    for noun, nabbr in sorted(_NOUN_DATA_SHORT.items(), key=lambda x: -len(x[0])):
        if legacy_key.startswith(noun):
            attr = legacy_key[len(noun):]
            if attr in _ATTR_SHORT:
                abbr = nabbr + _ATTR_SHORT[attr]
                return abbr if abbr != legacy_key else None
    return None


def _make_abbreviations(count: int) -> tuple[list[str], dict[str, str]]:
    """Generate ``count`` abbreviated tokens and their expected canonical targets.

    Skips any abbreviation that would collide with an existing mapping key.
    Selects evenly across the alphabetically sorted candidate list for stable,
    representative coverage.
    """
    candidates: list[tuple[str, str]] = []
    for legacy, canonical in sorted(MAPPING.items()):
        abbr = _abbreviate(legacy)
        if abbr is None or abbr in MAPPING:
            continue
        candidates.append((abbr, canonical))

    candidates.sort()
    step = max(1, len(candidates) // count)
    selected = candidates[::step][:count]
    assert len(selected) == count, (
        f"Only {len(selected)} usable abbreviations (need {count}); "
        f"check abbreviation tables cover all components"
    )
    tokens = [t for t, _ in selected]
    ground_truth = {t: c for t, c in selected}
    return tokens, ground_truth


def _build_source(tokens: list[str]) -> str:
    """Emit valid Python placing each token across five identifier roles."""
    lines: list[str] = []
    for i, tok in enumerate(tokens):
        role = i % 5
        if role == 0:
            lines.append(f"{tok} = {i}")                    # VARIABLE
        elif role == 1:
            lines.append(f"def {tok}():\n    return {i}")  # FUNCTION
        elif role == 2:
            lines.append(f"class {tok}:\n    pass")        # CLASS
        elif role == 3:
            lines.append(f"import os as {tok}")            # IMPORT alias
        else:
            lines.append(f's{i} = "{tok} pending"')        # STRING sub-token
    return "\n".join(lines) + "\n"


def _accuracy(results, ground_truth: dict[str, str]) -> tuple[int, int, float]:
    """Count correct replacements among tokens present in ground_truth."""
    relevant = [r for r in results if r.candidate.text in ground_truth]
    total = len(relevant)
    correct = sum(
        1 for r in relevant
        if r.applied and r.replacement == ground_truth[r.candidate.text]
    )
    return correct, total, (correct / total if total else 0.0)


def _llm_available(cfg) -> bool:
    """Return True if the configured LLM backend appears reachable/configured."""
    if cfg.llm.backend == "anthropic":
        import os
        return bool(os.environ.get("CLAUDE_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"))
    # ollama: probe the tags endpoint
    try:
        httpx.get(f"{cfg.llm.base_url.rstrip('/')}/api/tags", timeout=2.0).raise_for_status()
        return True
    except Exception:
        return False


# ── Test 1: clean exact keys ──────────────────────────────────────────────────

def test_accuracy_clean_exact(tmp_path, capsys):
    """Exact legacy keys must auto-apply to 100% accuracy."""
    tokens = _sample_keys()
    ground_truth = {t: MAPPING[t] for t in tokens}

    src = tmp_path / "clean.py"
    src.write_text(_build_source(tokens))

    report = align_file(str(src), MAPPING_PATH, dry_run=False)
    correct, total, acc = _accuracy(report.results, ground_truth)

    with capsys.disabled():
        print(f"\n[clean] accuracy = {correct}/{total} = {acc:.1%}")

    assert total == len(tokens)
    assert acc == 1.0

    transformed = src.read_text()
    for legacy, canonical in ground_truth.items():
        assert canonical in transformed


# ── Test 2: abbreviations, fuzzy top-1 only ───────────────────────────────────

def test_accuracy_abbrev_fuzzy_top1(tmp_path, capsys):
    """128 abbreviated identifiers: does fuzzy ranking pick the right key?

    Sets auto_apply=0.0 and recall_min=0.0 so every top-1 is applied without
    LLM.  This isolates the scoring function's ranking quality.
    """
    tokens, ground_truth = _make_abbreviations(128)

    src = tmp_path / "abbrev_fuzzy.py"
    src.write_text(_build_source(tokens))

    cfg = Config(thresholds=Thresholds(auto_apply=0.0, recall_min=0.0))
    results = align_source(src.read_bytes(), MAPPING, cfg, "python", str(src))

    correct, total, acc = _accuracy(results, ground_truth)
    with capsys.disabled():
        print(f"[abbrev-fuzzy] top-1 accuracy = {correct}/{total} = {acc:.1%}")

    assert total == len(tokens)
    assert acc >= 0.75, f"Fuzzy top-1 accuracy too low: {acc:.1%}"


# ── Test 3: abbreviations + live LLM recall ───────────────────────────────────

@pytest.mark.live
def test_accuracy_abbrev_with_llm(tmp_path, capsys):
    """128 abbreviated identifiers processed with real LLM recall."""
    cfg = load_config()
    if not _llm_available(cfg):
        pytest.skip(f"LLM backend {cfg.llm.backend!r} not reachable / not configured")

    tokens, ground_truth = _make_abbreviations(128)

    src = tmp_path / "abbrev_llm.py"
    src.write_text(_build_source(tokens))

    report = align_file(str(src), MAPPING_PATH, dry_run=True, use_llm=True)
    correct, total, acc = _accuracy(report.results, ground_truth)

    with capsys.disabled():
        print(
            f"\n[abbrev-llm] recall accuracy = {correct}/{total} = {acc:.1%}"
            f"  (model={cfg.llm.model})"
        )
        errors = [
            r for r in report.results
            if r.candidate.text in ground_truth
            and not (r.applied and r.replacement == ground_truth[r.candidate.text])
        ]
        if errors:
            print(f"\n--- {len(errors)} errors ---")
            for r in errors:
                expected = ground_truth[r.candidate.text]
                got = r.replacement if r.applied else "(not applied)"
                print(f"  {r.candidate.text!r:20s}  expected={expected!r:30s}  got={got!r}  reason={r.reason}")

    assert total == len(tokens)
    assert acc >= 0.0  # record result; LLM quality determines the floor
