"""Locate identifiers in source code with tree-sitter.

The AST gives us precise byte spans so renaming can be done by offset rather than
by regex, eliminating accidental over-/under-replacement. Each identifier is
tagged with an :class:`IdentifierType` that later decides its matching strategy.

Languages are described by a small :class:`LanguageSpec`; Python is supported
first and others can be added by registering a new spec.
"""

from __future__ import annotations

from dataclasses import dataclass

import tree_sitter_language_pack as tlp
from tree_sitter import Node, Parser

from .models import IdentifierCandidate, IdentifierType

# NOTE: tree_sitter_language_pack.get_parser() returns a broken wrapper on the
# pinned versions; build the Parser directly from the Language instead.


@dataclass(frozen=True)
class LanguageSpec:
    """Per-language node-type names used to classify identifiers."""

    name: str
    function_def: tuple[str, ...]   # nodes whose 'name' field is a function name
    class_def: tuple[str, ...]      # nodes whose 'name' field is a class/type name
    import_stmt: tuple[str, ...]    # statement nodes treated as imports
    string_content: tuple[str, ...]  # nodes holding raw string text
    identifier: tuple[str, ...] = ("identifier",)  # leaf identifier node types


PYTHON_SPEC = LanguageSpec(
    name="python",
    function_def=("function_definition",),
    class_def=("class_definition",),
    import_stmt=("import_statement", "import_from_statement"),
    string_content=("string_content",),
)

_SPECS: dict[str, LanguageSpec] = {"python": PYTHON_SPEC}

# map common file extensions to a registered language
_EXT_TO_LANG = {".py": "python"}


def detect_language(file_path: str) -> str | None:
    for ext, lang in _EXT_TO_LANG.items():
        if file_path.endswith(ext):
            return lang
    return None


class ASTAnalyzer:
    """Extract :class:`IdentifierCandidate` objects from source code."""

    def __init__(self, language: str):
        if language not in _SPECS:
            raise ValueError(f"unsupported language: {language!r}")
        self.spec = _SPECS[language]
        self.parser = Parser(tlp.get_language(language))

    def extract(self, source: bytes | str, file_path: str = "") -> list[IdentifierCandidate]:
        if isinstance(source, str):
            source = source.encode("utf-8")
        tree = self.parser.parse(source)
        lines = source.split(b"\n")

        candidates: list[IdentifierCandidate] = []
        claimed: set[int] = set()  # node ids already classified, to avoid dupes
        self._walk(tree.root_node, source, lines, file_path, "", candidates, claimed)
        candidates.sort(key=lambda c: c.start_byte)
        return candidates

    # -- traversal ---------------------------------------------------------

    def _walk(
        self,
        node: Node,
        source: bytes,
        lines: list[bytes],
        file_path: str,
        scope: str,
        out: list[IdentifierCandidate],
        claimed: set[int],
    ) -> None:
        spec = self.spec
        child_scope = scope

        if node.type in spec.function_def or node.type in spec.class_def:
            name = node.child_by_field_name("name")
            if name is not None:
                kind = (
                    IdentifierType.CLASS
                    if node.type in spec.class_def
                    else IdentifierType.FUNCTION
                )
                out.append(self._make(name, kind, lines, file_path, scope))
                claimed.add(name.id)
                nm = name.text.decode()
                child_scope = f"{scope}.{nm}" if scope else nm

        elif node.type in spec.import_stmt:
            for ident in self._descendant_identifiers(node):
                if ident.id not in claimed:
                    out.append(self._make(ident, IdentifierType.IMPORT, lines, file_path, scope))
                    claimed.add(ident.id)
            return  # nothing else inside an import worth visiting generically

        elif node.type in spec.string_content:
            out.append(self._make(node, IdentifierType.STRING, lines, file_path, scope))
            return

        elif node.type in spec.identifier and node.id not in claimed:
            out.append(self._make(node, IdentifierType.VARIABLE, lines, file_path, scope))
            claimed.add(node.id)
            return

        for child in node.children:
            self._walk(child, source, lines, file_path, child_scope, out, claimed)

    def _descendant_identifiers(self, node: Node) -> list[Node]:
        found: list[Node] = []
        stack = list(reversed(node.children))
        while stack:
            n = stack.pop()
            if n.type in self.spec.identifier:
                found.append(n)
            else:
                stack.extend(reversed(n.children))
        return found

    # -- helpers -----------------------------------------------------------

    def _make(
        self,
        node: Node,
        kind: IdentifierType,
        lines: list[bytes],
        file_path: str,
        scope: str,
    ) -> IdentifierCandidate:
        row = node.start_point[0]
        return IdentifierCandidate(
            text=node.text.decode("utf-8", errors="replace"),
            id_type=kind,
            file_path=file_path,
            line=row + 1,
            col_start=node.start_point[1],
            col_end=node.end_point[1],
            start_byte=node.start_byte,
            end_byte=node.end_byte,
            context=self._context(lines, row),
            scope=scope,
        )

    @staticmethod
    def _context(lines: list[bytes], row: int, radius: int = 2) -> str:
        lo = max(0, row - radius)
        hi = min(len(lines), row + radius + 1)
        return b"\n".join(lines[lo:hi]).decode("utf-8", errors="replace")
