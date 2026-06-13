import json

import pytest

mcp_server = pytest.importorskip("symbol_aligner.mcp_server")


def test_tools_registered():
    # FastMCP exposes registered tools via the async list_tools API.
    import anyio

    tools = anyio.run(mcp_server.mcp.list_tools)
    names = {t.name for t in tools}
    assert {
        "align_single_file",
        "preview_alignment",
        "align_batch",
        "query_candidates",
    } <= names


def test_query_candidates_tool():
    out = json.loads(mcp_server.query_candidates("getUser", "mappings/example.json"))
    assert out["identifier"] == "getUser"
    assert out["top_k"][0]["legacy"] == "getUser"
    assert out["top_k"][0]["score"] == 1.0


def test_preview_tool_does_not_write(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("x = getUser\n")
    out = json.loads(mcp_server.preview_alignment(str(f), "mappings/example.json"))
    assert f.read_text() == "x = getUser\n"
    assert out["dry_run"] is True
    assert any(c["new"] == "fetchClient" for c in out["changes"])


def test_align_batch_dry_run(tmp_path):
    (tmp_path / "a.py").write_text("x = getUser\n")
    (tmp_path / "b.py").write_text("y = setAccount\n")
    (tmp_path / "c.txt").write_text("z = getUser\n")  # ignored: not .py
    out = json.loads(mcp_server.align_batch(str(tmp_path), "mappings/example.json"))
    assert out["file_count"] == 2
    assert (tmp_path / "a.py").read_text() == "x = getUser\n"  # dry-run, untouched
