import pytest

from symbol_aligner.config import ScoringWeights
from symbol_aligner.fuzz_match import get_top_k, score

W = ScoringWeights()

MAPPING = {
    "UsrAcctBal": "UserAccountBalance",
    "get_usr_acct_bal": "get_user_account_balance",
    "usrAcctInfo": "userAccountInfo",
    "getUsrInf": "getUserInfo",
    "calcTtl": "calculateTotal",
    "tmpVal": "temporaryValue",
}


def test_exact_match_is_one():
    assert score("calcTtl", "calcTtl", W) == 1.0


def test_score_bounded():
    for key in MAPPING:
        s = score("usrAcctBal", key, W)
        assert 0.0 <= s <= 1.0


def test_typo_scores_high_against_intended_key():
    # dropped letter typo of "getUsrInf"
    top = get_top_k("getUsrInfo", MAPPING, W, k=3)
    assert top[0][0] == "getUsrInf"
    assert top[0][1] > 0.45


def test_top_k_respects_k():
    assert len(get_top_k("usrAcctBal", MAPPING, W, k=2)) == 2
    assert len(get_top_k("usrAcctBal", MAPPING, W, k=10)) == len(MAPPING)


def test_top_k_sorted_descending():
    scores = [s for _, s in get_top_k("usrAcctBal", MAPPING, W, k=6)]
    assert scores == sorted(scores, reverse=True)


def test_unrelated_query_scores_low():
    top = get_top_k("xyzzy", MAPPING, W, k=1)
    assert top[0][1] < 0.45


def test_tie_break_is_deterministic():
    # identical mapping over keys that would tie -> stable order by key
    m = {"bbb": "x", "aaa": "y"}
    top = get_top_k("zzz", m, W, k=2)
    if top[0][1] == top[1][1]:
        assert [k for k, _ in top] == ["aaa", "bbb"]
