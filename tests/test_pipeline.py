import pytest

from symbol_aligner.config import Config, Thresholds
from symbol_aligner.main import align_file, align_source, apply_changes, match_candidate
from symbol_aligner.mapping import load_mapping
from symbol_aligner.models import IdentifierCandidate, IdentifierType, MatchSource

MAPPING = {
    "getUsrInf": "getUserInfo",
    "tmpVal": "temporaryValue",
    "calcTtl": "calculateTotal",
}


def cand(text):
    return IdentifierCandidate(text, IdentifierType.VARIABLE, "x.py", 1, 0, len(text), 0, len(text))


def test_exact_match_auto_applies():
    r = match_candidate(cand("tmpVal"), MAPPING, Config())
    assert r.source is MatchSource.EXACT
    assert r.replacement == "temporaryValue"


def test_low_score_discarded():
    r = match_candidate(cand("xyzzy"), MAPPING, Config())
    assert r.source is MatchSource.NONE
    assert not r.applied


def test_recall_band_without_recaller_discards():
    # craft a config so a near-miss lands in the recall band, not auto-apply
    cfg = Config(thresholds=Thresholds(auto_apply=0.99, recall_min=0.45))
    r = match_candidate(cand("getUsrInfo"), MAPPING, cfg)  # typo of getUsrInf
    assert r.source is MatchSource.NONE
    assert "recall-band" in r.reason


def test_recall_band_uses_recaller():
    cfg = Config(thresholds=Thresholds(auto_apply=0.99, recall_min=0.45))
    seen = {}

    def recaller(candidate, top_k):
        seen["called"] = True
        key = top_k[0][0]
        from symbol_aligner.models import MatchResult
        return MatchResult(candidate, key, MAPPING[key], 0.8, MatchSource.LLM, "picked")

    r = match_candidate(cand("getUsrInfo"), MAPPING, cfg, recaller)
    assert seen.get("called")
    assert r.source is MatchSource.LLM
    assert r.replacement == "getUserInfo"


def test_apply_changes_by_offset():
    src = b"a = tmpVal\n"
    results = align_source(src, MAPPING, Config(), "python")
    out = apply_changes(src, results)
    assert out == b"a = temporaryValue\n"


def test_align_file_dry_run_does_not_write(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("x = tmpVal\n")
    report = align_file(str(f), "mappings/example.json", dry_run=True)
    assert f.read_text() == "x = tmpVal\n"  # untouched
    assert report.dry_run


def test_align_file_applies_when_not_dry_run(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("x = tmpVal\ny = calcTtl\n")
    align_file(str(f), "mappings/example.json", dry_run=False)
    out = f.read_text()
    assert "temporaryValue" in out
    assert "calculateTotal" in out


def test_report_dict_shape(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("x = tmpVal\n")
    report = align_file(str(f), "mappings/example.json", dry_run=True)
    d = report.to_dict()
    assert d["summary"]["auto"] >= 1
    assert any(ch["new"] == "temporaryValue" for ch in d["changes"])
