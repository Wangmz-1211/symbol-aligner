import textwrap

import pytest

from symbol_aligner.config import (
    Config,
    ScoringWeights,
    Thresholds,
    load_config,
)


def write(tmp_path, content):
    p = tmp_path / "config.toml"
    p.write_text(textwrap.dedent(content))
    return p


def test_load_defaults_from_repo_config():
    cfg = load_config()  # repo-root config.toml
    assert cfg.top_k == 3
    assert cfg.thresholds.auto_apply == 0.99
    assert cfg.thresholds.recall_min == 0.45
    assert cfg.llm.backend == "ollama"
    assert cfg.llm.model == "llama3.1:8b"


def test_missing_keys_fall_back_to_defaults(tmp_path):
    cfg = load_config(write(tmp_path, "[matching]\ntop_k = 5\n"))
    assert cfg.top_k == 5
    assert cfg.thresholds == Thresholds()
    assert cfg.weights == ScoringWeights()


def test_weights_must_sum_to_one(tmp_path):
    with pytest.raises(ValueError, match="sum to 1.0"):
        load_config(
            write(
                tmp_path,
                """
                [scoring.weights]
                ratio = 0.5
                partial_ratio = 0.5
                token_sort_ratio = 0.5
                jaro_winkler = 0.5
                """,
            )
        )


def test_threshold_ordering_enforced(tmp_path):
    with pytest.raises(ValueError, match="recall_min <= auto_apply"):
        load_config(
            write(
                tmp_path,
                "[thresholds]\nauto_apply = 0.4\nrecall_min = 0.9\n",
            )
        )


def test_top_k_must_be_positive(tmp_path):
    with pytest.raises(ValueError, match="top_k"):
        load_config(write(tmp_path, "[matching]\ntop_k = 0\n"))
