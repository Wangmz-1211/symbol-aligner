<!-- LANG-SWITCH -->
[English](./README.md) | [中文](./README.zh.md) | **日本語**

# Symbol Aligner

レガシーコード向けの、高信頼かつ低コストな識別子リネーム。AST が識別子を特定し、複数のファジーマッチングアルゴリズムの加重が高信頼度の変換を担い、LLM は**最後の**リコール手段としてのみ使われます。作業の大半は決定論的なアルゴリズムが行うため、安価で監査可能です。

## はじめに

本プロジェクトの背景と動機は [プロジェクトの背景](./STORY.ja.md) を参照してください。

## 設計

### 全体方針

パイプラインの核心原則：**AST が精密な位置特定を担い、ファジーマッチングが高信頼度の自動変換を担い、LLM はファジーマッチングが判断できないときの最後のリコール手段としてのみ使う。** 作業の大半は決定論的で伝統的なアルゴリズムが行い、コストを抑え、可監査性を保証します。

```
┌─────────────────────────────────────────────┐
│              MCP ツール層                     │
│   align_file | align_batch | preview | query │
└──────────────────────┬──────────────────────┘
                      │  main.py (パイプライン編成)
       ┌───────────────┼───────────────┐
       ▼               ▼               ▼
 ast_analyze.py   fuzz_match.py     recall.py
 (tree-sitter)    (rapidfuzz)       (LLM, フォールバック)
   識別子の特定    TopK スコアリング  低信頼度時に選択
       └───────────────┴───────────────┘
                      │
                 mapping.json
              (1 対 1 マッピング表)
```

### データ構造

#### マッピング表 `mapping.json`

マッピングは厳密に **1 対 1**：`legacy`（レガシーフィールドのマッチ用）→ `canonical`（標準化された新フィールド）。

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

読み込み後、マッチング用に `dict[str, str]`（`legacy → canonical`）へ平坦化されます。双方向とも一意でなければなりません。1 つの legacy が 2 つの canonical へ対応してはならず、2 つの legacy が同じ canonical へ対応してもいけません。

#### 識別子候補 `IdentifierCandidate`

```python
@dataclass
class IdentifierCandidate:
    text: str                # 元のテキスト
    id_type: IdentifierType  # VARIABLE / FUNCTION / CLASS / STRING / IMPORT
    file_path: str
    line: int                # 1 始まり
    col_start: int
    col_end: int
    start_byte: int          # 絶対バイトオフセット——置換の基準
    end_byte: int
    context: str             # 前後の数行。LLM リコール用
    scope: str               # スコープパス。例 "MyClass.my_method"
```

#### マッチ結果 `MatchResult`

```python
@dataclass
class MatchResult:
    candidate: IdentifierCandidate
    matched_key: str | None   # ヒットした legacy key
    replacement: str | None   # 対応する canonical
    confidence: float         # 0.0 ~ 1.0
    source: MatchSource       # EXACT / FUZZY / LLM / NONE
    reason: str               # 監査ログ用
```

### マッチングフロー

```
query 識別子
    │
    ▼
マッピング表の全 legacy フィールドと類似度を計算
    │
    ▼
TopK 候補を取得（k はデフォルト 3）
    │
    ▼
top-1 スコアで信頼度を分級 → 自動適用 / LLM リコール / 破棄
```

候補は「すべてのマッピングと現在の query との類似度」をソートした TopK で決まり、トークン化は行いません。唯一の例外は **文字列リテラル（`STRING`）** です。まず空白・記号で単純に `split` し、各トークンごとに TopK を実行して結果をマージします。それ以外の種類はすべて丸ごとマッチします。

### モジュール設計

#### `ast_analyze.py` — AST 解析

[tree-sitter](https://github.com/tree-sitter/tree-sitter) でソースを解析し、種類ごとに識別子候補を抽出します。`id_type` が後続で split が必要かを決めます。

| 識別子の種類 | tree-sitter ノード | 処理 |
| --- | --- | --- |
| `VARIABLE` | 代入・宣言中の `identifier` | 丸ごとマッチ |
| `FUNCTION` | `function_definition.name` | 丸ごとマッチ |
| `CLASS`    | `class_definition.name` | 丸ごとマッチ |
| `IMPORT`   | `import_statement` / `import_from_statement` 内の識別子 | 丸ごとマッチ |
| `STRING`   | `string_content` ノード | 単語に split してトークンごとにマッチ |

置換は AST ノードのバイトオフセットに基づいて行い（正規表現の全文置換ではなく）、誤置換や位置ずれを根本から排除します。言語は小さな `LanguageSpec` で記述され、**まず Python を登録**しており、言語の追加は新しい spec を足すだけです。

#### `fuzz_match.py` — ファジーマッチング

[RapidFuzz](https://github.com/rapidfuzz/RapidFuzz) を用い、複数アルゴリズムの加重スコアでスペルミス・母音省略・略語などの非標準的命名をカバーします。完全一致時は `1.0` へ短絡します。

```python
def score(query, key, weights) -> float:
    if query == key:
        return 1.0
    return (
        weights.ratio            * fuzz.ratio(query, key)             # Levenshtein: スペルミス
        + weights.partial_ratio    * fuzz.partial_ratio(query, key)   # 部分文字列: 略語
        + weights.token_sort_ratio * fuzz.token_sort_ratio(query, key)  # 順序非依存
        + weights.jaro_winkler     * (JaroWinkler.similarity(query, key) * 100.0)  # 接頭辞の加重
    ) / 100.0

def get_top_k(query, mapping, weights, k=3) -> list[tuple[str, float]]:
    scored = [(key, score(query, key, weights)) for key in mapping]
    scored.sort(key=lambda kv: (-kv[1], kv[0]))  # 同点は key 順。出力は安定
    return scored[:k]
```

#### `recall.py` — LLM リコール（フォールバック）

**top-1 スコアがリコール帯に入ったときのみ起動**し、パイプライン全体で LLM を呼ぶ唯一の箇所です。TopK 候補とコンテキストを LLM に渡し、提示した集合の中から**選ばせます**（置換を勝手に生成させません）。出力は厳密に制限された JSON で、トークン消費は最小限です。同じ `(text, context, 候補 keys)` の結果はキャッシュされます。

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

> 実測では `llama3.1:8b` は `legacy` key ではなく `canonical`（右側）を返すことがよくあります。マッピングが 1 対 1 であるため、リコールモジュールはどちらの側も受け付けますが、提示した候補集合の内側に厳密に限定し、堅牢性と可監査性を両立させます。集合外の回答は拒否として扱います。

#### `llm.py` — LLM クライアント抽象

最小限の `complete(prompt)` インターフェースをラップします。デフォルトのバックエンドはローカルの **Ollama** サーバーに接続し、他のバックエンドも同じプロトコルを実装できます。

### 信頼度の分級

ソースにハードコードされた閾値はなく、すべて設定ファイルから来ます。**人手レビューの段階はありません**。

| top-1 スコア | 処理 |
| --- | --- |
| ≥ `thresholds.auto_apply` (0.99) | top-1 を自動適用 |
| ≥ `thresholds.recall_min` (0.45) | TopK を LLM に渡してリコール |
| < `thresholds.recall_min` | 破棄 |

LLM リコーラーが指定されない場合、リコール帯の候補は単に破棄されます。

#### 設定ファイル `config.toml`

```toml
[matching]
top_k = 3

[thresholds]
auto_apply = 0.99   # 直接適用、LLM を経由しない
recall_min = 0.45   # これ未満は破棄

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

### MCP ツールインターフェース

```
align_single_file(file_path, mapping_path, config_path?, use_llm?)  # 単一ファイルを整列し変更レポートを返す
align_batch(directory, mapping_path, extensions?, config_path?, dry_run?, use_llm?)  # ディレクトリを一括処理
preview_alignment(file_path, mapping_path, config_path?, use_llm?)  # dry-run、プレビューのみ
query_candidates(identifier, mapping_path, config_path?)            # デバッグ: 単一識別子の TopK
```

### 監査レポート

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

### ディレクトリ構成

```
symbol-aligner/
├── src/                    # `symbol_aligner` パッケージとしてインポート可能
│   ├── main.py             # パイプライン編成 + CLI
│   ├── ast_analyze.py      # ASTAnalyzer: tree-sitter による解析と位置特定
│   ├── fuzz_match.py       # get_top_k / score: ファジースコアリング
│   ├── recall.py           # LLMRecall: 低信頼度リコール
│   ├── llm.py              # LLMClient 抽象（Ollama バックエンド）
│   ├── models.py           # IdentifierCandidate / MatchResult / AlignmentReport
│   ├── mapping.py          # マッピング表の読み込みと検証
│   ├── config.py           # config.toml の読み込み
│   └── mcp_server.py       # 4 つのツールを公開する FastMCP サーバー
├── mappings/example.json
├── config.toml
├── tests/
└── README.md
```

## 実装計画

各段階が独立してテスト可能になるよう段階的に実装します。外部サービスを必要としないコアアルゴリズムから始め、LLM と MCP は最後に組み込みます。

1. **基礎データ層** — `models.py`（データクラスと列挙）、`config.py`（`config.toml` の解析）、`mapping.py`（1 対 1 マッピング表の読み込みと検証）。外部依存なし、まず単体テスト。
2. **ファジーマッチングのコア** — `fuzz_match.py`：加重 `score` と `get_top_k`。「legacy → canonical」のサンプル群でランキングと閾値境界を検証する。全プロジェクトで最も重要で、最もテストカバレッジを要する部分。
3. **AST 解析** — `ast_analyze.py`：tree-sitter を統合し、まず単一言語（Python）に対応、種類ごとに `IdentifierCandidate` とノード座標を抽出。フィクスチャのソースで抽出と位置特定の正確性を検証する。
4. **パイプライン連結（LLM なし）** — `main.py`：解析 → TopK → 閾値分級 → 座標ベース置換 を連結し、まず `auto_apply` と `discard` の 2 段だけを処理して監査レポートを出力。この時点で実ファイルの高信頼度自動変換が可能になる。
5. **LLM リコール** — `llm.py` + `recall.py`：`[recall_min, auto_apply)` 帯のリコールロジックを補完し、結果キャッシュを追加。LLM 部分はモッククライアントでテストし、テストがネットワークに依存しないようにする。
6. **文字列の特別処理** — パイプラインに `STRING` 種の split 分岐を追加する。
7. **MCP ラッピング** — `align_single_file` / `align_batch` / `preview_alignment` / `query_candidates` の 4 ツールを公開し、dry-run プレビューを補う。
8. **仕上げ** — `pyproject.toml`、サンプルマッピング、エンドツーエンドテスト、README の使い方。

> 上記 8 段階はすべて実装済みです。コードは [src/](./src)、テストは [tests/](./tests) にあります。

## 使い方

### インストール

本プロジェクトは **Python 3.13** を対象とします（tree-sitter の C 拡張は 3.14 上で ABI の問題があります。下記「既知の問題」を参照）。

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,mcp]"
```

依存：`rapidfuzz`、`tree-sitter` + `tree-sitter-language-pack`、`httpx`；任意で `mcp`、`pytest`。

### コマンドライン

```bash
# プレビュー（dry-run、書き込みなし）。JSON 監査レポートを出力
python -m symbol_aligner.main path/to/file.py mappings/example.json

# 実際に書き込む
python -m symbol_aligner.main path/to/file.py mappings/example.json --apply

# LLM リコールを有効化（[recall_min, auto_apply) 帯の近似命名を処理）
python -m symbol_aligner.main path/to/file.py mappings/example.json --apply --use-llm
```

インストール後は `symbol-aligner` コンソールコマンドも使えます。

### LLM バックエンド

リコールはローカルの [Ollama](https://ollama.com/) サーバーを使い、デフォルトモデルは `llama3.1:8b`、設定は `config.toml` の `[llm]` にあります。

```bash
ollama pull llama3.1:8b   # 一度だけ
ollama serve              # デフォルトで http://localhost:11434 を待ち受け
```

`--use-llm` を付けない場合、LLM には一切触れず、リコール帯の候補は破棄されます。

### MCP サーバー

```bash
python -m symbol_aligner.mcp_server   # またはインストール済みの symbol-aligner-mcp
```

4 つのツールを公開します：`align_single_file`、`preview_alignment`、`align_batch`、`query_candidates`。

### 設定 `config.toml`

すべての閾値とスコアリング重みはここに集約され、コードにハードコードはありません。主な項目：`thresholds.auto_apply`（デフォルト 0.99、直接適用）、`thresholds.recall_min`（デフォルト 0.45、未満は破棄）、`matching.top_k`（デフォルト 3）、`scoring.weights`（4 つのアルゴリズム重み、合計が 1 でなければならない）。

### テスト

```bash
pytest                 # 全部。Ollama に接続できない場合に自動スキップされる live テストを含む
pytest -m "not live"   # live テストをスキップ
```

#### 正確率

[tests/test_accuracy.py](./tests/test_accuracy.py) は生成したソースファイルを変換し、ground truth と比較します。テストケースは実際の工学的な略語慣習（母音省略・ドメイン頭字語：`computeRisk → compRsk`、`setAccount → setAcct`）を使います。

| シナリオ | ケース数 | 正確率 |
| --- | --- | --- |
| clean——厳密な legacy key（自動適用パス） | 24 | **100%**（24/24） |
| abbrev——ファジー top-1、LLM なし（`auto_apply=0`、`recall_min=0`） | 128 | **98.4%**（126/128） |
| abbrev + LLM リコール——`claude-haiku-4-5` | 128 | **100%**（128/128） |
| abbrev + LLM リコール——`llama3.1:8b` | 128 | **83.6%**（107/128） |

**ファジー top-1 だけで略語ケースの 98.4% を LLM なしに解決できます。** 残り約 1.6% はリコール帯に落ち、ファジーランキングが確定できない場合に LLM が審判役として top-k 候補の中から最適なものを選びます（新たな候補を生成することはありません）。`claude-haiku-4-5` を使用すると、128 ケースのテストセットで**完璧な 100% マッチ**を達成します。LLM リコールはレイテンシが増えるためデフォルトは無効で、128 ケースの live テストはバックエンド未設定時に自動スキップされます。

### 既知の問題

- **Python 3.14**：`tree-sitter` 0.25.2 の C 拡張は 3.14 上で descriptor の挙動が異常になり（`tree.root_node` がメソッドオブジェクトを返す）、そのため 3.13 に固定しています。
- **`tree_sitter_language_pack.get_parser()`** は現在のバージョン組み合わせで壊れた parser を返すため、コードは `Parser(get_language(...))` を直接構築しています。
- AST 解析は現在 **Python** のみ登録しています。言語の追加は [src/ast_analyze.py](./src/ast_analyze.py) に `LanguageSpec` を 1 つ足すだけです。
