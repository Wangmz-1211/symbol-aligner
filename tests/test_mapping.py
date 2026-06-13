import json

import pytest

from symbol_aligner.mapping import MappingError, load_mapping


def write(tmp_path, obj):
    p = tmp_path / "mapping.json"
    p.write_text(json.dumps(obj))
    return p


def test_load_valid_mapping(tmp_path):
    m = load_mapping(
        write(
            tmp_path,
            {
                "version": 1,
                "mappings": [
                    {"legacy": "usr", "canonical": "user"},
                    {"legacy": "acct", "canonical": "account"},
                ],
            },
        )
    )
    assert m == {"usr": "user", "acct": "account"}


def test_loads_repo_example():
    m = load_mapping("mappings/example.json")
    assert m["getUsrInf"] == "getUserInfo"


def test_duplicate_legacy_rejected(tmp_path):
    with pytest.raises(MappingError, match="duplicate legacy"):
        load_mapping(
            write(
                tmp_path,
                {"mappings": [
                    {"legacy": "usr", "canonical": "user"},
                    {"legacy": "usr", "canonical": "username"},
                ]},
            )
        )


def test_duplicate_canonical_rejected(tmp_path):
    with pytest.raises(MappingError, match="mapped from both"):
        load_mapping(
            write(
                tmp_path,
                {"mappings": [
                    {"legacy": "usr", "canonical": "user"},
                    {"legacy": "user", "canonical": "user"},
                ]},
            )
        )


def test_empty_fields_rejected(tmp_path):
    with pytest.raises(MappingError, match="non-empty"):
        load_mapping(write(tmp_path, {"mappings": [{"legacy": "", "canonical": "user"}]}))


def test_missing_mappings_array_rejected(tmp_path):
    with pytest.raises(MappingError, match="mappings"):
        load_mapping(write(tmp_path, {"version": 1}))


def test_empty_table_rejected(tmp_path):
    with pytest.raises(MappingError, match="empty"):
        load_mapping(write(tmp_path, {"mappings": []}))
