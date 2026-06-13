import json

import pytest

from symbol_aligner.mapping import MappingError, load_mapping


def write(tmp_path, obj):
    p = tmp_path / "mapping.json"
    p.write_text(json.dumps(obj))
    return p


def test_load_valid_mapping(tmp_path):
    m = load_mapping(write(tmp_path, {"getUser": "fetchClient", "setAccount": "configurePortfolio"}))
    assert m == {"getUser": "fetchClient", "setAccount": "configurePortfolio"}


def test_loads_repo_example():
    m = load_mapping("mappings/example.json")
    assert m["getUser"] == "fetchClient"
    assert m["accountStatus"] == "ledgerState"


def test_duplicate_canonical_rejected(tmp_path):
    with pytest.raises(MappingError, match="mapped from both"):
        load_mapping(write(tmp_path, {"getUser": "fetchClient", "findUser": "fetchClient"}))


def test_empty_canonical_rejected(tmp_path):
    with pytest.raises(MappingError, match="non-empty"):
        load_mapping(write(tmp_path, {"getUser": ""}))


def test_non_dict_rejected(tmp_path):
    with pytest.raises(MappingError, match="JSON object"):
        load_mapping(write(tmp_path, [{"legacy": "getUser", "canonical": "fetchClient"}]))


def test_empty_table_rejected(tmp_path):
    with pytest.raises(MappingError, match="empty"):
        load_mapping(write(tmp_path, {}))
