from symbol_aligner.config import Config
from symbol_aligner.main import align_source, apply_changes, string_subcandidates
from symbol_aligner.models import IdentifierCandidate, IdentifierType

MAPPING = {"tmpVal": "temporaryValue", "calcTtl": "calculateTotal"}


def str_cand(text, start_byte=0):
    return IdentifierCandidate(
        text, IdentifierType.STRING, "x.py", 1, 0, len(text), start_byte, start_byte + len(text)
    )


def test_split_produces_word_tokens():
    subs = string_subcandidates(str_cand("run tmpVal now"))
    assert [s.text for s in subs] == ["run", "tmpVal", "now"]


def test_split_spans_are_exact():
    text = "a tmpVal b"
    subs = string_subcandidates(str_cand(text, start_byte=100))
    tok = next(s for s in subs if s.text == "tmpVal")
    # offset of "tmpVal" within "a tmpVal b" is 2
    assert tok.start_byte == 102
    assert tok.end_byte == 108


def test_split_multibyte_prefix_offsets():
    text = "你好 tmpVal"  # CJK prefix is 3 bytes each
    subs = string_subcandidates(str_cand(text, start_byte=0))
    tok = next(s for s in subs if s.text == "tmpVal")
    # "你好 " == 3+3+1 = 7 bytes before the token
    assert tok.start_byte == 7
    assert text.encode()[tok.start_byte:tok.end_byte].decode() == "tmpVal"


def test_only_matching_token_in_string_is_replaced():
    src = 'log = "please calcTtl carefully"\n'.encode()
    results = align_source(src, MAPPING, Config(), "python")
    out = apply_changes(src, results).decode()
    assert out == 'log = "please calculateTotal carefully"\n'


def test_non_matching_words_left_intact():
    src = 'log = "hello world here"\n'.encode()
    results = align_source(src, MAPPING, Config(), "python")
    out = apply_changes(src, results).decode()
    assert out == 'log = "hello world here"\n'  # untouched
