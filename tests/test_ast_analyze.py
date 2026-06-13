import pytest

from symbol_aligner.ast_analyze import ASTAnalyzer, detect_language
from symbol_aligner.models import IdentifierType

SRC = '''class FooBar:
    def get_usr(self, usrAcctBal):
        tmpVal = usrAcctBal + 1
        msg = "hello usrAcctInfo world"
        return tmpVal

import calcTtl
from mod import getUsrInf as g
'''


@pytest.fixture
def candidates():
    return ASTAnalyzer("python").extract(SRC, "x.py")


def by_type(cands, t):
    return [c.text for c in cands if c.id_type is t]


def test_detect_language():
    assert detect_language("a/b/c.py") == "python"
    assert detect_language("a/b/c.txt") is None


def test_unsupported_language_raises():
    with pytest.raises(ValueError, match="unsupported"):
        ASTAnalyzer("cobol")


def test_class_name_extracted(candidates):
    assert "FooBar" in by_type(candidates, IdentifierType.CLASS)


def test_function_name_extracted(candidates):
    assert "get_usr" in by_type(candidates, IdentifierType.FUNCTION)


def test_variables_extracted(candidates):
    vars_ = by_type(candidates, IdentifierType.VARIABLE)
    assert "tmpVal" in vars_
    assert "usrAcctBal" in vars_


def test_string_content_extracted(candidates):
    strings = by_type(candidates, IdentifierType.STRING)
    assert "hello usrAcctInfo world" in strings  # quotes excluded


def test_imports_extracted(candidates):
    imports = by_type(candidates, IdentifierType.IMPORT)
    assert "calcTtl" in imports
    assert "getUsrInf" in imports


def test_byte_span_round_trips(candidates):
    src_bytes = SRC.encode()
    for c in candidates:
        assert src_bytes[c.start_byte:c.end_byte].decode() == c.text


def test_candidates_sorted_by_position(candidates):
    starts = [c.start_byte for c in candidates]
    assert starts == sorted(starts)


def test_scope_tracking(candidates):
    tmp = next(c for c in candidates if c.text == "tmpVal" and c.id_type is IdentifierType.VARIABLE)
    assert "get_usr" in tmp.scope


def test_no_duplicate_spans(candidates):
    spans = [(c.start_byte, c.end_byte) for c in candidates]
    assert len(spans) == len(set(spans))
