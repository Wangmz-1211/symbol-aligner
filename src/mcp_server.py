"""MCP server exposing Symbol Aligner as reusable tools.

Run with::

    python -m symbol_aligner.mcp_server

The tools are thin wrappers over the pipeline in :mod:`symbol_aligner.main`;
all heavy lifting (AST, fuzzy matching, optional LLM recall) lives there.
"""

from __future__ import annotations

import json
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .ast_analyze import detect_language
from .config import load_config
from .fuzz_match import get_top_k
from .mapping import load_mapping
from .main import align_file

mcp = FastMCP("symbol-aligner")


@mcp.tool()
def align_single_file(
    file_path: str,
    mapping_path: str,
    config_path: str | None = None,
    use_llm: bool = False,
) -> str:
    """Align one source file in place and return a JSON change report."""
    report = align_file(
        file_path, mapping_path, config_path, dry_run=False, use_llm=use_llm
    )
    return json.dumps(report.to_dict(), ensure_ascii=False, indent=2)


@mcp.tool()
def preview_alignment(
    file_path: str,
    mapping_path: str,
    config_path: str | None = None,
    use_llm: bool = False,
) -> str:
    """Dry-run: report the changes that *would* be made, without writing."""
    report = align_file(
        file_path, mapping_path, config_path, dry_run=True, use_llm=use_llm
    )
    return json.dumps(report.to_dict(), ensure_ascii=False, indent=2)


@mcp.tool()
def align_batch(
    directory: str,
    mapping_path: str,
    extensions: list[str] | None = None,
    config_path: str | None = None,
    dry_run: bool = True,
    use_llm: bool = False,
) -> str:
    """Align every matching file under a directory; returns a summary report."""
    exts = tuple(extensions or [".py"])
    reports = []
    for path in sorted(Path(directory).rglob("*")):
        if path.is_file() and path.suffix in exts and detect_language(str(path)):
            report = align_file(
                str(path), mapping_path, config_path,
                dry_run=dry_run, use_llm=use_llm,
            )
            d = report.to_dict()
            if d["changes"]:
                reports.append(d)
    return json.dumps(
        {"dry_run": dry_run, "files": reports, "file_count": len(reports)},
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool()
def query_candidates(
    identifier: str,
    mapping_path: str,
    config_path: str | None = None,
) -> str:
    """Debug tool: show the top-k fuzzy candidates for a single identifier."""
    config = load_config(config_path)
    mapping = load_mapping(mapping_path)
    top_k = get_top_k(identifier, mapping, config.weights, k=config.top_k)
    return json.dumps(
        {
            "identifier": identifier,
            "top_k": [
                {"legacy": k, "canonical": mapping[k], "scores": d}
                for k, d in top_k
            ],
            "thresholds": {
                "auto_apply": config.thresholds.auto_apply,
                "recall_min": config.thresholds.recall_min,
            },
        },
        ensure_ascii=False,
        indent=2,
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
