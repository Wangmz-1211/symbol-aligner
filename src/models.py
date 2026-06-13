"""Core data structures shared across the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class IdentifierType(str, Enum):
    """The syntactic kind of an extracted identifier.

    The type decides matching strategy: every kind is matched whole, except
    ``STRING`` which is split on whitespace/punctuation and matched per token.
    """

    VARIABLE = "VARIABLE"
    FUNCTION = "FUNCTION"
    CLASS = "CLASS"
    IMPORT = "IMPORT"
    STRING = "STRING"


class MatchSource(str, Enum):
    """How a match was decided."""

    EXACT = "EXACT"   # query equals a legacy key verbatim
    FUZZY = "FUZZY"   # accepted on fuzzy score alone (>= auto_apply)
    LLM = "LLM"       # picked by the LLM from the top-k candidates
    NONE = "NONE"     # no replacement (discarded / no candidate)


@dataclass
class IdentifierCandidate:
    """A single identifier occurrence located in source code."""

    text: str
    id_type: IdentifierType
    file_path: str
    line: int           # 1-based
    col_start: int      # 0-based byte column, inclusive
    col_end: int        # 0-based byte column, exclusive
    start_byte: int = 0  # absolute byte offset in the source, inclusive
    end_byte: int = 0    # absolute byte offset in the source, exclusive
    context: str = ""   # surrounding lines, for LLM recall
    scope: str = ""     # e.g. "MyClass.my_method"


@dataclass
class MatchResult:
    """The outcome of matching one candidate against the mapping table."""

    candidate: IdentifierCandidate
    matched_key: str | None      # the legacy key that was hit
    replacement: str | None      # the corresponding canonical value
    confidence: float            # 0.0 .. 1.0
    source: MatchSource
    reason: str = ""             # human-readable note for the audit log

    @property
    def applied(self) -> bool:
        """Whether this result yields an actual text change."""
        return self.replacement is not None and self.matched_key is not None


@dataclass
class AlignmentReport:
    """Per-file summary of an alignment run."""

    file_path: str
    results: list[MatchResult] = field(default_factory=list)
    dry_run: bool = False

    def _count(self, source: MatchSource) -> int:
        return sum(1 for r in self.results if r.applied and r.source is source)

    def to_dict(self) -> dict:
        changes = []
        discarded = 0
        for r in self.results:
            if r.applied:
                changes.append(
                    {
                        "line": r.candidate.line,
                        "col": r.candidate.col_start,
                        "old": r.candidate.text,
                        "new": r.replacement,
                        "confidence": round(r.confidence, 3),
                        "source": r.source.value,
                        "reason": r.reason,
                    }
                )
            else:
                discarded += 1
        return {
            "file": self.file_path,
            "dry_run": self.dry_run,
            "summary": {
                "auto": self._count(MatchSource.FUZZY) + self._count(MatchSource.EXACT),
                "llm": self._count(MatchSource.LLM),
                "discarded": discarded,
            },
            "changes": changes,
        }
