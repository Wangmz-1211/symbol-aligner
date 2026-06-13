<!-- LANG-SWITCH -->
[English](./README.md) | **中文** | [日本語](./README.ja.md)

# Symbol Aligner

面向遗留代码的高可靠、低成本标识符重命名：AST 负责定位标识符，多种模糊匹配算法的加权负责高置信度的转换，LLM **仅**作为最后的召回手段。绝大部分工作由确定性算法完成，因此既便宜又可审计。

## 简介

项目的背景与动机请参见 [项目故事](./STORY.zh.md)。

## 设计

### 总体思路

整个流程的核心原则：**AST 负责精确定位，模糊匹配负责高置信度的自动转换，LLM 仅作为模糊匹配无法定夺时的最后召回手段。** 绝大部分工作由确定性的传统算法完成，以此控制成本并保证可审计性。

```
┌─────────────────────────────────────────────┐
│              MCP Tool 接口层                  │
│   align_file | align_batch | preview | query │
└──────────────────────┬──────────────────────┘
                      │  main.py (Pipeline 编排)
       ┌───────────────┼───────────────┐
       ▼               ▼               ▼
 ast_analyze.py   fuzz_match.py     recall.py
 (tree-sitter)    (rapidfuzz)       (LLM, fallback)
   定位标识符      TopK 召回打分      低置信度时选择
       └───────────────┴───────────────┘
                      │
                 mapping.json
              (1对1 映射表)
```

### 数据结构

#### 映射表 `mapping.json`

映射关系是严格的 **1 对 1**：`legacy`（用于匹配遗留字段）→ `canonical`（规范的新字段）。

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

加载后展平为 `dict[str, str]`（`legacy → canonical`）供匹配使用。双向均须唯一：一个 legacy 不能映射到两个 canonical，两个 legacy 也不能映射到同一个 canonical。

#### 标识符候选 `IdentifierCandidate`

```python
@dataclass
class IdentifierCandidate:
    text: str                # 原始文本
    id_type: IdentifierType  # VARIABLE / FUNCTION / CLASS / STRING / IMPORT
    file_path: str
    line: int                # 从 1 开始
    col_start: int
    col_end: int
    start_byte: int          # 绝对字节偏移——替换的依据
    end_byte: int
    context: str             # 前后数行，供 LLM 召回使用
    scope: str               # 作用域路径，如 "MyClass.my_method"
```

#### 匹配结果 `MatchResult`

```python
@dataclass
class MatchResult:
    candidate: IdentifierCandidate
    matched_key: str | None   # 命中的 legacy key
    replacement: str | None   # 对应的 canonical
    confidence: float         # 0.0 ~ 1.0
    source: MatchSource       # EXACT / FUZZY / LLM / NONE
    reason: str               # 审计日志用
```

### 匹配流程

```
query 标识符
    │
    ▼
对映射表全量 legacy 字段计算相似度
    │
    ▼
取 TopK 候选（k 默认 3）
    │
    ▼
按 top-1 得分进行置信度分级 → 自动应用 / LLM 召回 / 丢弃
```

候选字段由「所有映射关系与当前 query 字段的相似度」排序后的 TopK 决定，不做 tokenize。唯一的例外是 **字符串字面量（`STRING`）**：先按空格 / 标点做一次简单 `split`，对每个分词分别跑 TopK 再合并，其余类型一律整体匹配。

### 模块设计

#### `ast_analyze.py` — AST 解析

使用 [tree-sitter](https://github.com/tree-sitter/tree-sitter) 解析源码，按类型提取标识符候选。不同 `id_type` 决定后续是否需要 split：

| 标识符类型 | tree-sitter 节点 | 处理 |
| --- | --- | --- |
| `VARIABLE` | 赋值 / 声明中的 `identifier` | 整体匹配 |
| `FUNCTION` | `function_definition.name` | 整体匹配 |
| `CLASS`    | `class_definition.name` | 整体匹配 |
| `IMPORT`   | `import_statement` / `import_from_statement` 内的标识符 | 整体匹配 |
| `STRING`   | `string_content` 节点 | 简单 split 后逐词匹配 |

基于 AST 节点字节坐标做替换（而非正则全文替换），从根本上避免误替换与位置偏移。语言由一个小的 `LanguageSpec` 描述，**目前先注册了 Python**，新增语言只需增加一个 spec。

#### `fuzz_match.py` — 模糊匹配

使用 [RapidFuzz](https://github.com/rapidfuzz/RapidFuzz)，多算法加权打分，覆盖拼写错误、省略元音、缩写等多种非标准命名。完全相等时短路为 `1.0`：

```python
def score(query, key, weights) -> float:
    if query == key:
        return 1.0
    return (
        weights.ratio            * fuzz.ratio(query, key)             # Levenshtein：拼写错误
        + weights.partial_ratio    * fuzz.partial_ratio(query, key)   # 子串：缩写
        + weights.token_sort_ratio * fuzz.token_sort_ratio(query, key)  # 忽略顺序
        + weights.jaro_winkler     * (JaroWinkler.similarity(query, key) * 100.0)  # 前缀加权
    ) / 100.0

def get_top_k(query, mapping, weights, k=3) -> list[tuple[str, float]]:
    scored = [(key, score(query, key, weights)) for key in mapping]
    scored.sort(key=lambda kv: (-kv[1], kv[0]))  # 同分按 key 排序，输出稳定
    return scored[:k]
```

#### `recall.py` — LLM 召回（fallback）

**仅当 top-1 得分落在召回区间时触发**，是全流程唯一调用 LLM 的环节。把 TopK 候选与上下文交给 LLM，让其从候选集中**选择**（不让它凭空生成替换），输出严格受限的 JSON，token 消耗极小。相同 `(text, context, 候选 keys)` 的结果会被缓存。

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

> 实测中 `llama3.1:8b` 常常回传 `canonical`（映射右侧）而非 `legacy` key。由于映射是 1对1 的，召回模块对两侧都接受，且严格限制在候选集合内，以此兼顾鲁棒性与可审计性。集合外的回答按拒绝处理。

#### `llm.py` — LLM 客户端抽象

封装统一的 `complete(prompt)` 接口。默认后端连接本地 **Ollama** 服务，其他后端可实现同一协议。

### 置信度分级

所有阈值不在代码中硬编码，全部来自配置文件。**无任何人工审核环节**：

| top-1 得分 | 处理方式 |
| --- | --- |
| ≥ `thresholds.auto_apply` (0.99) | 自动应用 top-1 |
| ≥ `thresholds.recall_min` (0.45) | 转交 LLM 从 TopK 中召回 |
| < `thresholds.recall_min` | 直接丢弃 |

未提供 LLM 召回器时，落在召回区间的候选直接丢弃。

#### 配置文件 `config.toml`

```toml
[matching]
top_k = 3

[thresholds]
auto_apply = 0.99   # 直接应用，不经 LLM
recall_min = 0.45   # 低于此值直接丢弃

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

### MCP Tool 接口

```
align_single_file(file_path, mapping_path, config_path?, use_llm?)  # 单文件对齐，返回变更报告
align_batch(directory, mapping_path, extensions?, config_path?, dry_run?, use_llm?)  # 批量处理目录
preview_alignment(file_path, mapping_path, config_path?, use_llm?)  # dry-run，仅预览不落盘
query_candidates(identifier, mapping_path, config_path?)            # 调试：查询单个标识符的 TopK
```

### 审计报告

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

### 目录结构

```
symbol-aligner/
├── src/                    # 作为 `symbol_aligner` 包导入
│   ├── main.py             # Pipeline 编排 + CLI
│   ├── ast_analyze.py      # ASTAnalyzer：tree-sitter 解析与定位
│   ├── fuzz_match.py       # get_top_k / score：模糊匹配打分
│   ├── recall.py           # LLMRecall：低置信度召回
│   ├── llm.py              # LLMClient 抽象（Ollama 后端）
│   ├── models.py           # IdentifierCandidate / MatchResult / AlignmentReport
│   ├── mapping.py          # 映射表加载与校验
│   ├── config.py           # config.toml 加载
│   └── mcp_server.py       # FastMCP 服务，暴露四个工具
├── mappings/example.json
├── config.toml
├── tests/
└── README.md
```

## 实现计划

分阶段实现，每一阶段都可独立测试，从不依赖外部服务的核心算法开始，最后再接入 LLM 与 MCP。

1. **基础数据层** — `models.py`（数据类与枚举）、`config.py`（解析 `config.toml`）、`mapping.py`（加载校验 1对1 映射表）。无外部依赖，先写单元测试。
2. **模糊匹配核心** — `fuzz_match.py`：实现 `score` 加权打分与 `get_top_k`。用一组「遗留命名 → 规范命名」样例验证打分排序与阈值边界，这是全项目最关键、最需要测试覆盖的部分。
3. **AST 解析** — `ast_analyze.py`：接入 tree-sitter，先支持单一语言（Python），实现按类型提取 `IdentifierCandidate` 及节点坐标。用 fixture 源文件验证提取与定位准确性。
4. **Pipeline 串联（不含 LLM）** — `main.py`：把 解析 → TopK → 阈值分级 → 基于坐标替换 串起来，先只处理 `auto_apply` 与 `discard` 两档，输出审计报告。此时已可对真实文件做高置信度自动转换。
5. **LLM 召回** — `llm.py` + `recall.py`：补齐 `recall_min ~ auto_apply` 区间的召回逻辑，加入结果缓存。LLM 部分用 mock client 做测试，避免测试依赖网络。
6. **字符串特殊处理** — 在 Pipeline 中加入 `STRING` 类型的 split 分支。
7. **MCP 封装** — 暴露 `align_single_file` / `align_batch` / `preview_alignment` / `query_candidates` 四个工具，补充 `dry-run` 预览。
8. **打磨** — `pyproject.toml`、示例映射表、端到端测试、README 使用说明。

> 上述八个阶段均已实现，对应代码见 [src/](./src)，测试见 [tests/](./tests)。

## 使用

### 安装

项目针对 **Python 3.13**（tree-sitter 的 C 扩展在 3.14 上存在 ABI 问题，详见下方“已知问题”）。

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,mcp]"
```

依赖：`rapidfuzz`、`tree-sitter` + `tree-sitter-language-pack`、`httpx`；可选 `mcp`、`pytest`。

### 命令行

```bash
# 预览（dry-run，不写文件），输出 JSON 审计报告
python -m symbol_aligner.main path/to/file.py mappings/example.json

# 实际写入
python -m symbol_aligner.main path/to/file.py mappings/example.json --apply

# 启用 LLM 召回（处理落在 [recall_min, auto_apply) 区间的近似命名）
python -m symbol_aligner.main path/to/file.py mappings/example.json --apply --use-llm
```

安装后亦可直接用 `symbol-aligner` 控制台命令。

### LLM 后端

召回使用本地 [Ollama](https://ollama.com/)，默认模型 `llama3.1:8b`，配置见 `config.toml` 的 `[llm]`：

```bash
ollama pull llama3.1:8b   # 一次性
ollama serve              # 默认监听 http://localhost:11434
```

未加 `--use-llm` 时完全不接触 LLM，落在召回区间的候选直接丢弃。

### MCP 服务

```bash
python -m symbol_aligner.mcp_server   # 或安装后的 symbol-aligner-mcp
```

暴露四个工具：`align_single_file`、`preview_alignment`、`align_batch`、`query_candidates`。

### 配置 `config.toml`

所有阈值与打分权重集中在此，代码中无硬编码。关键项：`thresholds.auto_apply`（默认 0.99，直接应用）、`thresholds.recall_min`（默认 0.45，低于则丢弃）、`matching.top_k`（默认 3）、`scoring.weights`（四种算法权重，须和为 1）。

### 测试

```bash
pytest                 # 全部，含一个连不上 Ollama 时自动跳过的 live 用例
pytest -m "not live"   # 跳过 live 用例
```

### 已知问题

- **Python 3.14**：`tree-sitter` 0.25.2 的 C 扩展在 3.14 上 descriptor 行为异常（`tree.root_node` 返回方法对象），故固定使用 3.13。
- **`tree_sitter_language_pack.get_parser()`** 在当前版本组合下返回损坏的 parser，代码改为用 `Parser(get_language(...))` 直接构造。
- 目前 AST 解析仅注册了 **Python**；新增语言只需在 [src/ast_analyze.py](./src/ast_analyze.py) 增加一个 `LanguageSpec`。
