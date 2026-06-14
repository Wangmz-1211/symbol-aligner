<!-- LANG-SWITCH -->
**English** | [中文](./README.zh.md) | [日本語](./README.ja.md)

# Symbol Aligner

Reliable, low-cost identifier renaming for legacy code: AST locates identifiers, a
weighted blend of fuzzy-matching algorithms does the high-confidence conversions, and an
LLM is used **only** as a last-resort recall step. Most of the work is done by
deterministic algorithms, which keeps it cheap and auditable.

## Introduction

See the [story](./STORY.md) for the background and motivation behind this project.

## Design

### Overall idea

The core principle of the pipeline: **AST handles precise location, fuzzy matching handles
high-confidence automatic conversion, and the LLM is only the last-resort recall step when
fuzzy matching cannot decide.** The vast majority of the work is done by deterministic,
traditional algorithms, in order to control cost and guarantee auditability.

```
┌─────────────────────────────────────────────┐
│                 MCP tool layer                │
│   align_file | align_batch | preview | query │
└──────────────────────┬──────────────────────┘
                      │  main.py (pipeline orchestration)
       ┌───────────────┼───────────────┐
       ▼               ▼               ▼
 ast_analyze.py   fuzz_match.py     recall.py
 (tree-sitter)    (rapidfuzz)       (LLM, fallback)
   locate ids      top-k scoring     pick on low confidence
       └───────────────┴───────────────┘
                      │
                 mapping.json
              (1-to-1 mapping table)
```

### Data structures

#### Mapping table `mapping.json`

The mapping is strictly **1-to-1**: `legacy` (used to match the legacy field) → `canonical`
(the standardized new field).

```json
{
  "version": 1,
  "mappings": [
    { "legacy": "UsrAcctBal",       "canonical": "UserAccountBalance" },
    { "legacy": "get_usr_acct_bal", "canonical": "get_user_account_balance" },
    { "legacy": "usrAcctInfo",      "canonical": "userAccountInfo" }
  ]
}
```

It is flattened into a `dict[str, str]` (`legacy → canonical`) for matching. Both
directions must be unique: a legacy key cannot map to two canonicals, and two legacy keys
cannot map to the same canonical.

#### Identifier candidate `IdentifierCandidate`

```python
@dataclass
class IdentifierCandidate:
    text: str                # original text
    id_type: IdentifierType  # VARIABLE / FUNCTION / CLASS / STRING / IMPORT
    file_path: str
    line: int                # 1-based
    col_start: int
    col_end: int
    start_byte: int          # absolute byte offset — the basis for replacement
    end_byte: int
    context: str             # surrounding lines, for LLM recall
    scope: str               # scope path, e.g. "MyClass.my_method"
```

#### Match result `MatchResult`

```python
@dataclass
class MatchResult:
    candidate: IdentifierCandidate
    matched_key: str | None   # the legacy key that was hit
    replacement: str | None   # the corresponding canonical
    confidence: float         # 0.0 .. 1.0
    source: MatchSource       # EXACT / FUZZY / LLM / NONE
    reason: str               # for the audit log
```

### Matching flow

```
query identifier
    │
    ▼
score against every legacy key in the mapping
    │
    ▼
take the top-k candidates (k defaults to 3)
    │
    ▼
grade by the top-1 score → auto-apply / LLM recall / discard
```

Candidates are determined by the top-k of "similarity between every mapping and the current
query", with no tokenization. The only exception is **string literals (`STRING`)**: they
are first `split` on whitespace/punctuation, each token runs top-k separately, and the
results are merged. Every other kind is matched whole.

### Module design

#### `ast_analyze.py` — AST analysis

Parses source with [tree-sitter](https://github.com/tree-sitter/tree-sitter) and extracts
identifier candidates by kind. The `id_type` decides whether splitting is needed later:

| Identifier kind | tree-sitter node | Handling |
| --- | --- | --- |
| `VARIABLE` | `identifier` in assignments/declarations | matched whole |
| `FUNCTION` | `function_definition.name` | matched whole |
| `CLASS`    | `class_definition.name` | matched whole |
| `IMPORT`   | identifiers inside `import_statement` / `import_from_statement` | matched whole |
| `STRING`   | `string_content` node | split into words, then matched per token |

Replacement is done by AST node byte offsets (not regex find-and-replace), which
fundamentally eliminates wrong replacements and positional drift. Languages are described
by a small `LanguageSpec`; **Python is registered first**, and adding a language only
requires adding a new spec.

#### `fuzz_match.py` — fuzzy matching

Uses [RapidFuzz](https://github.com/rapidfuzz/RapidFuzz) with a multi-algorithm weighted
score, covering spelling errors, dropped vowels, abbreviations, and other non-standard
naming. An exact match short-circuits to `1.0`.

```python
def score(query, key, weights) -> float:
    if query == key:
        return 1.0
    return (
        weights.ratio            * fuzz.ratio(query, key)             # Levenshtein: typos
        + weights.partial_ratio    * fuzz.partial_ratio(query, key)   # substring: abbreviations
        + weights.token_sort_ratio * fuzz.token_sort_ratio(query, key)  # order-insensitive
        + weights.jaro_winkler     * (JaroWinkler.similarity(query, key) * 100.0)  # prefix weighting
    ) / 100.0

def get_top_k(query, mapping, weights, k=3) -> list[tuple[str, float]]:
    scored = [(key, score(query, key, weights)) for key in mapping]
    scored.sort(key=lambda kv: (-kv[1], kv[0]))  # ties broken by key, so output is stable
    return scored[:k]
```

#### `recall.py` — LLM recall (fallback)

**Triggered only when the top-1 score lands in the recall band**; it is the one and only
place in the whole pipeline that calls the LLM. The top-k candidates and the context are
handed to the LLM, which **chooses** among the offered set (it does not invent a
replacement); the output is tightly constrained JSON, so token usage is minimal. Results
for the same `(text, context, candidate keys)` are cached.

```
You are a code symbol mapping assistant.
The identifier below is a non-standard (legacy) name that may correspond to one of the
candidate mappings. Decide which candidate, if any, the identifier is a misspelling/
abbreviation of, using the surrounding context.

Return ONLY a JSON object. "key" MUST be copied verbatim from the left-hand "legacy"
column of one candidate line below:
  {"key": "<exact legacy token>", "confidence": <0.0-1.0>}
If none fits, return {"key": null, "confidence": 0.0}

Identifier: {text}
Type: {id_type}
Context:
{context}

Candidates (legacy -> canonical):
{candidates}
```

> In practice `llama3.1:8b` often returns the `canonical` (right-hand side) instead of the
> `legacy` key. Because the mapping is 1-to-1, the recall module accepts either side, while
> still staying strictly within the offered candidate set — balancing robustness and
> auditability. An out-of-set answer is treated as a rejection.

#### `llm.py` — LLM client abstraction

Wraps a minimal `complete(prompt)` interface. The default backend talks to a local
**Ollama** server; other backends can implement the same protocol.

### Confidence grading

No threshold is hard-coded in the source — they all come from the config file. There is
**no human-review tier**:

| top-1 score | Handling |
| --- | --- |
| ≥ `thresholds.auto_apply` (0.99) | auto-apply top-1 |
| ≥ `thresholds.recall_min` (0.45) | hand the top-k to the LLM for recall |
| < `thresholds.recall_min` | discard |

When no LLM recaller is supplied, candidates in the recall band are simply discarded.

#### Config file `config.toml`

```toml
[matching]
top_k = 3

[thresholds]
auto_apply = 0.99   # apply directly, no LLM
recall_min = 0.45   # below this -> discard

[llm]
backend    = "ollama"
base_url   = "http://localhost:11434"
model      = "llama3.1:8b"
max_tokens = 64
timeout    = 30.0
cache      = true

[scoring.weights]
ratio            = 0.40
partial_ratio    = 0.25
token_sort_ratio = 0.20
jaro_winkler     = 0.15
```

### MCP tool interface

```
align_single_file(file_path, mapping_path, config_path?, use_llm?)  # align one file, return change report
align_batch(directory, mapping_path, extensions?, config_path?, dry_run?, use_llm?)  # process a directory
preview_alignment(file_path, mapping_path, config_path?, use_llm?)  # dry-run, preview only
query_candidates(identifier, mapping_path, config_path?)            # debug: top-k for one identifier
```

### Audit report

```json
{
  "file": "src/service/usrAcctService.ts",
  "summary": { "auto": 42, "llm": 5, "discarded": 3 },
  "changes": [
    { "line": 12, "old": "usrAcctBal", "new": "userAccountBalance",
      "confidence": 0.997, "source": "FUZZY" },
    { "line": 34, "old": "getUsrInf", "new": "getUserInfo",
      "confidence": 0.81, "source": "LLM",
      "reason": "LLM selected 'getUsrInf' from top-3 (conf 0.81)" }
  ]
}
```

### Directory layout

```
symbol-aligner/
├── src/                    # importable as the `symbol_aligner` package
│   ├── main.py             # pipeline orchestration + CLI
│   ├── ast_analyze.py      # ASTAnalyzer: tree-sitter parsing & location
│   ├── fuzz_match.py       # get_top_k / score: fuzzy scoring
│   ├── recall.py           # LLMRecall: low-confidence recall
│   ├── llm.py              # LLMClient abstraction (Ollama backend)
│   ├── models.py           # IdentifierCandidate / MatchResult / AlignmentReport
│   ├── mapping.py          # mapping table loading & validation
│   ├── config.py           # config.toml loading
│   └── mcp_server.py       # FastMCP server exposing the four tools
├── mappings/example.json
├── config.toml
├── tests/
└── README.md
```

## Implementation Plan

The implementation is staged so that each stage is independently testable, starting from
the core algorithms that need no external service and bringing in the LLM and MCP last.

1. **Base data layer** — `models.py` (data classes & enums), `config.py` (parse
   `config.toml`), `mapping.py` (load & validate the 1-to-1 table). No external deps;
   unit tests first.
2. **Fuzzy-matching core** — `fuzz_match.py`: the weighted `score` and `get_top_k`.
   Validate ranking and threshold boundaries with a set of "legacy → canonical" samples;
   this is the most critical part and needs the most test coverage.
3. **AST analysis** — `ast_analyze.py`: integrate tree-sitter, support a single language
   (Python) first, extract `IdentifierCandidate`s and node coordinates. Validate
   extraction and location with fixture sources.
4. **Pipeline (no LLM)** — `main.py`: wire parse → top-k → threshold grading →
   offset-based replacement; handle only the `auto_apply` and `discard` tiers first, and
   emit the audit report. High-confidence auto-conversion of real files already works here.
5. **LLM recall** — `llm.py` + `recall.py`: fill in the recall logic for the
   `[recall_min, auto_apply)` band, with result caching. Test the LLM part with a mock
   client so tests do not depend on the network.
6. **String handling** — add the `STRING` split branch to the pipeline.
7. **MCP wrapping** — expose `align_single_file` / `align_batch` / `preview_alignment` /
   `query_candidates`, plus the dry-run preview.
8. **Polish** — `pyproject.toml`, example mapping, end-to-end tests, README usage.

> All eight stages are implemented. The code is under [src/](./src), and the tests are
> under [tests/](./tests).

## Usage

### Install

The project targets **Python 3.13** (tree-sitter's C extension has an ABI issue on 3.14;
see Known issues below).

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,mcp]"
```

Dependencies: `rapidfuzz`, `tree-sitter` + `tree-sitter-language-pack`, `httpx`; optional
`mcp`, `pytest`.

### Command line

```bash
# Preview (dry-run, no writes), prints a JSON audit report
python -m symbol_aligner.main path/to/file.py mappings/example.json

# Actually write
python -m symbol_aligner.main path/to/file.py mappings/example.json --apply

# Enable LLM recall (handles near-misses in the [recall_min, auto_apply) band)
python -m symbol_aligner.main path/to/file.py mappings/example.json --apply --use-llm
```

After install, the `symbol-aligner` console command is also available.

### LLM backend

Recall uses a local [Ollama](https://ollama.com/) server, default model `llama3.1:8b`,
configured under `[llm]` in `config.toml`:

```bash
ollama pull llama3.1:8b   # one-time
ollama serve              # listens on http://localhost:11434 by default
```

Without `--use-llm`, the LLM is never touched and recall-band candidates are discarded.

### MCP server

```bash
python -m symbol_aligner.mcp_server   # or the installed symbol-aligner-mcp
```

Exposes four tools: `align_single_file`, `preview_alignment`, `align_batch`,
`query_candidates`.

### Config `config.toml`

All thresholds and scoring weights live here; nothing is hard-coded. Key items:
`thresholds.auto_apply` (default 0.99, apply directly), `thresholds.recall_min` (default
0.45, discard below it), `matching.top_k` (default 3), `scoring.weights` (four algorithm
weights, must sum to 1).

### Testing

```bash
pytest                 # everything, including live tests that auto-skip if Ollama is unreachable
pytest -m "not live"   # skip the live tests
```

#### Accuracy

[tests/test_accuracy.py](./tests/test_accuracy.py) transforms a generated source file and
compares the result against ground truth. Test cases use real-world abbreviation conventions
(dropping vowels, domain acronyms: `computeRisk → compRsk`, `setAccount → setAcct`).

| Scenario | Cases | Accuracy |
| --- | --- | --- |
| clean — exact legacy keys (auto-apply path) | 24 | **100%** (24/24) |
| abbrev — fuzzy top-1, no LLM (`auto_apply=0`, `recall_min=0`) | 128 | **98.4%** (126/128) |
| abbrev + LLM recall — `claude-haiku-4-5` | 128 | **100%** (128/128) |
| abbrev + LLM recall — `llama3.1:8b` | 128 | **100%** (128/128) |

**Fuzzy top-1 alone resolves 98.4% of abbreviation cases without touching the LLM.** The
remaining ~1.6% fall into the recall band where the identifier is ambiguous enough that
fuzzy ranking cannot commit — here the LLM acts as a tie-breaker, choosing among the top-k
candidates it is offered (it never invents a replacement). With `claude-haiku-4-5`, recall
achieves a **perfect 100% on the 128-case test set**. LLM recall adds latency and is off by
default; the 128-case live test is skipped unless a backend is configured.

### Known issues

- **Python 3.14**: `tree-sitter` 0.25.2's C extension behaves incorrectly on 3.14
  (descriptor access is scrambled — `tree.root_node` returns a method object), so the
  project pins 3.13.
- **`tree_sitter_language_pack.get_parser()`** returns a broken parser on the current
  version combination; the code constructs `Parser(get_language(...))` directly instead.
- AST analysis currently registers **Python** only; adding a language only requires a new
  `LanguageSpec` in [src/ast_analyze.py](./src/ast_analyze.py).
