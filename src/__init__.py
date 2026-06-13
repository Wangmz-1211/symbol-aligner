"""Symbol Aligner: reliable identifier renaming via AST + fuzzy matching.

The LLM is used only as a fallback recall step; the bulk of the work is done by
deterministic algorithms to keep the process cheap and auditable.
"""

__version__ = "0.1.0"
