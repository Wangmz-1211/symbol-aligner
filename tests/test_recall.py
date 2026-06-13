import httpx
import pytest

from symbol_aligner.config import load_config
from symbol_aligner.llm import OllamaClient, build_client
from symbol_aligner.models import IdentifierCandidate, IdentifierType, MatchSource
from symbol_aligner.recall import LLMRecall

MAPPING = {"getUsrInf": "getUserInfo", "calcTtl": "calculateTotal"}
TOP_K = [("getUsrInf", 0.8), ("calcTtl", 0.5)]


def cand(text="getUsrInfo", context="profile = getUsrInfo()"):
    return IdentifierCandidate(text, IdentifierType.FUNCTION, "x.py", 1, 0, len(text), 0, len(text), context)


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    def complete(self, prompt):
        self.calls += 1
        return self.response


def test_recall_selects_offered_key():
    r = LLMRecall(FakeClient('{"key": "getUsrInf", "confidence": 0.9}'), MAPPING)
    res = r.recall(cand(), TOP_K)
    assert res.source is MatchSource.LLM
    assert res.replacement == "getUserInfo"
    assert res.confidence == 0.9


def test_recall_null_rejects():
    r = LLMRecall(FakeClient('{"key": null, "confidence": 0.0}'), MAPPING)
    res = r.recall(cand(), TOP_K)
    assert res.source is MatchSource.NONE
    assert not res.applied


def test_recall_accepts_canonical_echo():
    # Models often return the canonical (right-hand) value instead of the legacy
    # key; since the mapping is 1:1 this still unambiguously identifies the pair.
    r = LLMRecall(FakeClient('{"key": "getUserInfo", "confidence": 0.8}'), MAPPING)
    res = r.recall(cand(), TOP_K)
    assert res.source is MatchSource.LLM
    assert res.matched_key == "getUsrInf"
    assert res.replacement == "getUserInfo"


def test_recall_out_of_set_key_rejected():
    r = LLMRecall(FakeClient('{"key": "somethingElse", "confidence": 0.99}'), MAPPING)
    res = r.recall(cand(), TOP_K)
    assert res.source is MatchSource.NONE
    assert "out-of-set" in res.reason


def test_recall_handles_chatty_wrapping():
    # model wraps JSON in prose -> still extracted
    r = LLMRecall(FakeClient('Sure! Here is the answer:\n{"key": "getUsrInf", "confidence": 0.7} hope that helps'), MAPPING)
    res = r.recall(cand(), TOP_K)
    assert res.matched_key == "getUsrInf"


def test_recall_invalid_json_rejected():
    r = LLMRecall(FakeClient("not json at all"), MAPPING)
    res = r.recall(cand(), TOP_K)
    assert res.source is MatchSource.NONE


def test_recall_failure_does_not_raise():
    class Boom:
        def complete(self, prompt):
            raise RuntimeError("connection refused")

    r = LLMRecall(Boom(), MAPPING)
    res = r.recall(cand(), TOP_K)
    assert res.source is MatchSource.NONE
    assert "failed" in res.reason


def test_cache_avoids_second_call():
    client = FakeClient('{"key": "getUsrInf", "confidence": 0.9}')
    r = LLMRecall(client, MAPPING, cache=True)
    r.recall(cand(), TOP_K)
    r.recall(cand(), TOP_K)
    assert client.calls == 1


def test_cache_disabled_calls_each_time():
    client = FakeClient('{"key": "getUsrInf", "confidence": 0.9}')
    r = LLMRecall(client, MAPPING, cache=False)
    r.recall(cand(), TOP_K)
    r.recall(cand(), TOP_K)
    assert client.calls == 2


# -- live Ollama smoke test (skipped if the server isn't reachable) ---------

def _ollama_up(cfg):
    try:
        httpx.get(f"{cfg.llm.base_url.rstrip('/')}/api/tags", timeout=2.0).raise_for_status()
        return True
    except Exception:
        return False


@pytest.mark.live
def test_live_ollama_recall():
    cfg = load_config()
    if not _ollama_up(cfg):
        pytest.skip("Ollama server not reachable")
    client = build_client(cfg.llm)
    r = LLMRecall(client, MAPPING, cache=False)
    res = r.recall(cand(), TOP_K)
    # We don't assert which key (model-dependent), only that it stays in-set
    # and produces a structurally valid result.
    assert res.source in (MatchSource.LLM, MatchSource.NONE)
    if res.source is MatchSource.LLM:
        assert res.matched_key in dict(TOP_K)
