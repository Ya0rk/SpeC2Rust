# 确定性结构修复调研

## 研究问题

LLM 生成的 Rust 文件常见结构性损坏包括末尾截断、缺失闭括号、多余独立闭括号，以及 markdown fence 污染。此类错误会阻塞 rustc 暴露更深层的类型和接口诊断。确定性结构修复层的研究问题是：在不调用 LLM 的情况下，能否安全修复一部分纯语法结构错误，并且避免把字符串、注释、raw string 或 char 字面量中的括号误判为代码结构。

## 流程 / 数据流

`RustRepairAgent._sanitize_project_locally()` 会在每轮开始和每次 LLM 编辑后执行。它对 `.rs`、`Cargo.toml`、`README.md` 做本地清洗，然后调用 `_apply_deterministic_bracket_repair()` 扫描项目下所有 `.rs` 文件，跳过 `target/`。

单文件结构修复由 `rust_structural_repair.orchestrator.try_deterministic_repair()` 完成：

1. 读取 Rust 文件。
2. `scan_brackets()` 先通过 `mask_non_code()` 屏蔽字符串、注释、char 字面量等非代码区域，再栈式扫描括号。
3. 如果文件已经平衡，直接返回 `already_balanced`。
4. `apply_all_rules()` 最多执行 3 遍规则：R1 处理文件末尾未闭合，R2 删除孤立闭括号行。
5. `is_safe_repair()` 要求修复前不平衡、修复后完全平衡，才允许写回文件。
6. 返回 `RepairOutcome`，包含 `changed`、`description`、`details`、`pre_imbalance`、`post_imbalance`。

## 关键工程细节

- **非代码区屏蔽。** `mask_non_code()` 返回与原文等长的字符串，字符串、注释和 char 字面量被替换为空格，换行保留。后续扫描器的偏移、行号和列号仍能映射回原文件。
- **Rust 词法覆盖。** 支持行注释、嵌套块注释、普通字符串、byte string、raw string、byte raw string、char、byte char，并区分 lifetime（如 `'a`、`'static`）和 char 字面量。
- **栈式括号扫描。** 扫描结果区分 `unclosed_opens`、`orphan_closes` 和 `mismatches`。未闭合开括号记录原始缩进，用于追加闭括号。
- **R1 截断闭合。** 仅当存在未闭合开括号，且没有孤立闭括号、没有类型不匹配时执行；按栈逆序追加 `)`、`]`、`}`，缩进对齐开括号所在行。
- **R2 孤立闭括号删除。** 只删除「独占一行」的孤立闭括号。inline 孤立闭括号留给 LLM，避免破坏表达式语义。
- **安全写回。** 修复后必须 bracket-balanced，且修复前必须不平衡，否则不写文件。规则设计要求幂等，重复运行不应继续改动。
- **与 LLM 编辑联动。** LLM 每次编辑后都会再次触发本地清洗和结构修复，因此结构层可以修补 LLM 输出造成的轻微截断或多余闭括号，再交给 rustc 继续检查。

## 可引用代码证据

| 证据点 | 代码位置 |
| --- | --- |
| `RustRepairAgent` 每轮本地清洗后调用确定性修复 | `src/agent/rust_repair_agent.py:400`、`src/agent/rust_repair_agent.py:432` |
| 遍历 `.rs` 文件并跳过 `target/` | `src/agent/rust_repair_agent.py:434` |
| tokenizer 设计说明：屏蔽非代码区且保留行列 | `src/agent/rust_structural_repair/tokenizer.py:1` |
| `mask_non_code()` 支持注释、字符串、raw string、char 和 lifetime | `src/agent/rust_structural_repair/tokenizer.py:27`、`src/agent/rust_structural_repair/tokenizer.py:68`、`src/agent/rust_structural_repair/tokenizer.py:101` |
| 栈式扫描和结果类型 | `src/agent/rust_structural_repair/bracket_scanner.py:21`、`src/agent/rust_structural_repair/bracket_scanner.py:64` |
| R1 末尾截断闭合规则 | `src/agent/rust_structural_repair/repair_rules.py:26` |
| R2 孤立闭括号删除规则 | `src/agent/rust_structural_repair/repair_rules.py:75` |
| 多遍规则应用 | `src/agent/rust_structural_repair/repair_rules.py:120` |
| 安全校验：修复后平衡且修复前不平衡 | `src/agent/rust_structural_repair/validator.py:20`、`src/agent/rust_structural_repair/validator.py:35` |
| 单文件 orchestrator 读取、扫描、修复、验证、写回 | `src/agent/rust_structural_repair/orchestrator.py:39` |
| 项目级 helper | `src/agent/rust_structural_repair/orchestrator.py:103` |
| smoke test 覆盖字符串 / 注释括号、lifetime、R1、R2、幂等 | `src/agent/rust_structural_repair/_smoke_test.py:47`、`src/agent/rust_structural_repair/_smoke_test.py:63`、`src/agent/rust_structural_repair/_smoke_test.py:75`、`src/agent/rust_structural_repair/_smoke_test.py:100`、`src/agent/rust_structural_repair/_smoke_test.py:157` |

## 实验钩子

- **单元验证：** 运行 `python -m src.agent.rust_structural_repair._smoke_test`，验证字符串 / 注释中的括号不会被统计，R1 / R2 和幂等性成立。
- **端到端验证：** 运行 `python -m src.agent.rust_structural_repair._e2e_test`，构造截断 Rust 文件并验证 `try_deterministic_repair()` 写回后平衡。
- **流水线消融：** 暂时禁用 `_apply_deterministic_bracket_repair()`，统计语法阻塞数量、LLM 修复次数、最终通过率变化。
- **收益指标：** 记录每个项目中 `RepairOutcome.changed=True` 的文件数、`description` 分布、`pre_imbalance -> post_imbalance`，并关联后续 `syntax_blockers` 是否下降。
- **误判压力测试：** 构造含大量 `{}`、`[]`、`() ` 的 raw string、C 代码模板、注释块、char 和 lifetime 的 Rust 文件，确认结构修复不会把非代码区括号计入不平衡。

## 局限与反例

- 该层只保证括号平衡，不保证 Rust AST 合法；例如补上的 `}` 可能语法平衡但语义作用域错误。
- R1 在复杂截断场景中只能机械追加闭括号，无法判断缺失的是表达式、分号、match arm 还是完整函数体。
- R2 只删除独占一行的孤立闭括号；inline 多余括号、类型不匹配括号和交错嵌套错误都会跳过。
- tokenizer 覆盖了常见 Rust 字面量，但仍可能遗漏极端语法组合、宏 token tree 或未来 Rust 词法扩展。
- `RustRepairAgent._apply_deterministic_bracket_repair()` 目前只打印 `RepairOutcome`，没有写入 `repair_journal.jsonl`。论文实验若要量化收益，需要补充结构修复事件记录。
- `RustRepairAgent._brace_imbalance()` 在结构化编辑审计中仍是简单字符计数，可能被字符串 / 注释中的括号影响；可作为后续工程优化，把它替换为 `scan_brackets()`。

## 可写入论文位置

- **方法章节：** 「Layer 1 确定性语法结构修复」小节，强调非代码区屏蔽和保守规则。
- **工程优化章节：** 作为降低 LLM 修复负担的本地规则层，说明它在每轮开始和每次编辑后运行。
- **实验章节：** 做结构修复消融，报告语法阻塞下降、LLM 调用减少和误修率。
