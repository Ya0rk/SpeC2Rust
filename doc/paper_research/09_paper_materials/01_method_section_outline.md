# 方法章节提纲草案

## 用途

本文为后续论文方法章节提供可直接展开的结构。核心叙事是：本系统不是单次 C 到 Rust 代码生成器，而是一个由静态事实抽取、文档化约束、按需 Rust 生成、编译前沿修复、C 参考行为测试和运行时证据组成的闭环迁移框架。

论文主线以 `scripts/agent.sh` 当前默认流程为准：

```text
SpecAgent
  -> ContextualRustAgent
  -> RustRepairAgent
  -> RustTestAgent
```

早期 `CDocAgent -> RustAgent -> CodeFixer -> TestFixer` 可作为 baseline 或系统演进背景，不应作为方法章节主线。

## 内容结构

### 1. Problem Formulation：C 项目级迁移任务

建议回答的问题：

- 输入是什么：一个可构建的 C 项目、原始源码、头文件、Makefile、shell 测试和可选 README。
- 输出是什么：一个 Rust crate，默认由 `Cargo.toml`、入口文件和若干 `src/*.rs` 组成。
- 目标是什么：不是逐行转写，而是在 Rust 项目中复现 C 项目的可观察行为，并通过 `cargo build --release` 和原始 shell 测试验证。
- 外部 oracle 是什么：C 参考程序、原始 shell 测试、Rust 编译器和可选 LLDB / static probe。

可写的关键句：

> We formulate C-to-Rust migration as an evidence-guided project rewrite problem rather than a single prompt translation task.

待补证数据：

- 数据集项目数量、源码规模、测试数量、是否包含 CLI / library / mixed 项目。
- C 预检失败项目数量。
- 输入项目的平均函数数、宏数、结构体数、shell test 数。

局限：

- 当前系统依赖 C 项目可 clean/build；不可构建项目无法形成可靠 C 参考 oracle。
- 原始 shell 测试需要预处理到可在 Rust wrapper 环境中执行。

### 2. System Overview：多阶段闭环架构

建议把系统概述写成 1 张主图和 1 张阶段表。阶段顺序如下：

1. `scripts/agent.sh` 建立实验运行环境、默认 flags、输出目录和日志。
2. `main.py` 执行 C 项目预检。
3. `SpecAgent` 生成 C 项目理解文档、模块级 spec/plan/tasks 和 `translation_contract.json`。
4. `ContextualRustAgent` 按文件计划、文档索引和符号注册表生成 Rust 工程。
5. `UnfinishedCodeAgent` 在编译修复前补全明显占位。
6. `RustRepairAgent` 使用 `cargo check` 和 release build 反馈修复编译错误。
7. `RustTestAgent` 用 C shell tests 和 C 参考 binary 做功能修复。
8. `RoundLogger`、`translation_metrics.json`、`repair_journal.jsonl` 和 runtime evidence 支撑审计。

可引用系统机制：

- `scripts/agent.sh` 默认开启 `--use-spec-agent`、`--use-contextual-rust-agent`、`--use-rust-repair-agent`、`--use-rust-test-agent`、`--use-error-organizer-agent`。
- `src/agent/main.py` 中的阶段 gate、Rust 生成器互斥、release build gate 和 `translation_metrics` 保存。
- `log/round_logs/` 记录每轮 LLM request / reply、调用栈、耗时和 token 估算。

待补证数据：

- 每阶段耗时和 LLM 请求数。
- 默认链路与关闭修复 / 测试链路的端到端成功率。
- round log 与主日志之间的 run ID 对齐方式。

局限：

- 当前 `translation_metrics.json` 只有总耗时和总 LLM 请求数，不能直接给出阶段级成本。
- 主日志是文本，不是结构化事件流。

### 3. Static Fact Extraction：基于 tree-sitter 的 C 事实抽取

本节说明系统如何把 C 源码转成可追溯的函数、调用、结构体、宏和位置事实。建议不要把它写成“完整 C 语义分析器”，而应写成“无需完整编译数据库的轻量事实层”。

内容结构：

- `CCodeAnalyzer.analyze_directory()` 遍历 `.c` / `.h`。
- tree-sitter 抽取函数定义、源码 span、函数体、调用点。
- 文本扫描补充 `#define` 宏和部分 inline 函数。
- `project_analysis` 进入 `SpecAgent._build_dependency_graph()`，支撑调用图、结构体使用和模块切分。

可引用系统机制：

- `func_defid`、`span`、`source`、`calls`、`num_lines` 等函数事实字段。
- tree-sitter C 语言加载的多版本兼容逻辑。
- 宏扫描支持反斜杠续行，但不执行完整 preprocessor。

待补证数据：

- 函数、结构体、宏、调用边抽取覆盖率。
- 位置 span 抽样准确率。
- 函数指针、宏调用、条件编译造成的漏检比例。

局限：

- 不展开宏和条件编译。
- 函数指针、复杂声明器、匿名结构体和 typedef alias 仍可能不准。

### 4. Hierarchical Module Decomposition：面向 prompt 预算的模块切分

本节强调 `ModuleSplitter` 的目标不是传统编译器切片，而是为 LLM 生成和文档压缩提供规模可控、职责可解释的迁移单元。

内容结构：

- 输入：`project_info`、`project_analysis`、`dependency_graph`。
- 先按 `.c` 文件目录得到候选模块。
- 绑定函数和结构体，计算 `internal_calls`、`external_calls`、`cohesion_score`。
- 大模块按共享结构体、函数名前缀、文件局部性继续拆分。
- 输出 `module_units` 和 `cluster_units`。

可引用系统机制：

- `MAX_MODULE_FILES`、`MAX_MODULE_FUNCTIONS`、`MAX_CLUSTER_FUNCTIONS`、`MAX_CLUSTER_LINES` 等阈值。
- `cohesion_score = internal_calls / (internal_calls + external_calls)`。
- schema 归一化把 `func_defid/span/num_lines` 转成稳定字段。

待补证数据：

- 模块数量、平均函数数、平均行数、内聚度分布。
- 目录基线 vs 当前切分策略的文档长度和下游成功率。
- 结构体聚类、前缀聚类、文件局部性三类信号的消融。

局限：

- `.h` heavy 或 header-only 项目切分质量可能低。
- 同名 `static` 函数、函数指针和宏生成调用会影响调用图。

### 5. Documentation-mediated Context Construction：文档和契约作为控制信号

本节是方法章节的核心之一。建议把 `SpecAgent` 写成“将 C 事实转换为下游可执行约束的文档编译器”。

内容结构：

- 确定性文档：`00_repo_manifest.md`、`01_subsystems/*.md`、`02_interfaces/*.md`。
- LLM 文档：`03_behaviors`、`constitution.md`、模块级 `spec.md`、`plan.md`、`tasks.md`。
- 机器可读契约：`translation_contract.json`。
- 文档 lint：`translation_lint.json` 检查越界能力、未授权依赖、contract 外文件和 FFI。
- 可选风险证据：`PointerAgent`、`MacroAgent` 生成模块级 `pointer.md` / `macro.md`。

可引用系统机制：

- `translation_contract.json` 的 `generation_boundary.allowed_rust_files`、`dependency_policy`、`forbidden_without_evidence`。
- 模块级 spec / plan / tasks 与 C 文件、函数、类型事实绑定。
- prompt 约束禁止无证据扩展线程安全、恢复机制、序列化、FFI、benchmark 和发布任务。

待补证数据：

- contract 中函数、类型、宏覆盖率。
- `translation_lint.json` 中 scope expansion / out-of-scope file 数量。
- 启用 contract 前后越界文件和未授权依赖数量。
- Pointer / Macro 证据对所有权错误、宏错误、修复轮数的影响。

局限：

- `translation_contract.json` 依赖前置 C 分析质量。
- 文档 lint 当前主要报告，不一定阻断所有越界内容。
- `SpecAgent` 若干上下文预算常量仍偏大，论文中应避免过度声称强 hard-budget 压缩。

### 6. Demand-driven Rust Generation：按需上下文生成与符号护栏

本节说明 `ContextualRustAgent` 如何替代全文 prompt。建议将贡献命名为 `Demand-driven Rust Generation with Symbol Guardrails`。

内容结构：

- 加载 `SpecDocumentIndex` 和 `RustGenerationSpecAgent`。
- 从 contract / source records 推导文件计划，并应用 `--rust-entry-kind main|lib|auto`。
- 每个 Rust 文件只接收相关 spec section、C source snippets、文件计划和 registry summary。
- 模型可用 `<CGR_READ>` 请求 `spec`、`source`、`rust`、`registry`、`plan`。
- 写入前执行 contract lint、Rust 风格 lint、重复定义检查和引用检查。
- 本地生成 `Cargo.toml` 和 `src/lib.rs`，降低格式和 re-export 漂移。

可引用系统机制：

- `RustFilePlan` 字段：`path`、`role`、`owns`、`depends_on`、`source_files`、`source_functions`。
- `RustProjectRegistry` 抽取模块、类型、函数、常量、字段、方法和可见性。
- `<CGR_READ>` 多轮读取协议与每轮材料预算。
- contract lint 禁止 raw pointer、`unsafe`、`c_void`、`repr(C)`、未授权依赖和 FFI 泄漏。

待补证数据：

- `<CGR_READ>` 请求类型分布。
- registry 开启 / 关闭时重复定义和 unresolved import 数量。
- `ContextualRustAgent` vs `RustAgent`、`StableRustAgent`、`GrowthRustAgent` 的 prompt token、请求数、初始编译率。
- `src/lib.rs` 本地生成消融。

局限：

- registry 与 lint 主要是正则启发式，不是完整 Rust AST。
- 默认脚本强制 `--rust-entry-kind main`，对纯 library 项目可能不最优。
- 文件计划不能新增 contract 外合理拆分，可能过度收缩。

### 7. Compiler-feedback Repair：编译前沿驱动的修复

本节说明 `RustRepairAgent` 如何把 rustc 诊断转成多轮修复状态机。建议把重点放在“先诊断再编辑、结构化编辑、错误分批、前沿验收”。

内容结构：

- 基线：先运行 `cargo check`。
- 本地清洗：去 markdown fence、清理明显污染、执行确定性括号修复。
- `ErrorOrganizerAgent` 将 rustc 输出按错误码和文件分批。
- LLM 先输出诊断计划，再请求 Rust / C / spec 材料，最后输出结构化编辑。
- 编辑后重新 `cargo check`。
- 若通过 check，再运行 `cargo build --release`。
- 用 `error_count`、`error_signature`、`frontier_metrics` 判断是否推进编译前沿。
- `repair_journal.jsonl` 记录每轮过程。

可引用系统机制：

- `syntax_blockers` 优先于 `interface_blockers` 的验收顺序。
- 错误签名变化且错误数未暴涨时可接受为新前沿。
- `replace_range`、`insert_after`、`copy_range_after`、`copy_c_string_array_after` 等结构化编辑。
- `ErrorOrganizerAgent` 默认 `batch_size = 10`，每批附带真实行号源码窗口。
- `rust_structural_repair` 使用非代码区屏蔽后做括号扫描和保守修复。

待补证数据：

- `repair_journal.jsonl` 中 `accept_reason` 分布。
- 结构修复触发次数、语法阻塞下降数量。
- ErrorOrganizer batch size 消融。
- naive error-count 验收 vs blocker-aware frontier 验收。
- `RustRepairAgent` vs legacy `CodeFixer`。

局限：

- 默认 `in_place=True` 时，前沿验收不是完整事务回滚。
- `error_signature` 使用进程内 hash，跨实验统计需换稳定哈希。
- release build 通过不等于行为正确。

### 8. Functional Repair with C Reference Tests：C 行为驱动的功能修复

本节说明 `RustTestAgent` 如何把原 C 项目的 shell 测试转成 Rust 行为 oracle，并在失败时进行证据驱动修复。

内容结构：

- 构建 C 参考 binary。
- 复制 C `test/` 目录到 Rust 项目，脚本作为只读基准。
- `TestRunner` stage Rust / C wrapper。
- 首次运行全套 shell 测试。
- 针对失败用例创建 snapshot，进入修复循环。
- LLM 可请求 C source、Rust source、test artifact、debug probe、static probe，或提交 edits。
- 每次 edit 后先 `cargo build --release`，再重跑当前用例。
- 当前用例通过后检查 baseline pass cases 是否回归。

可引用系统机制：

- `BASH_ENV` 函数映射项目同名命令到 Rust binary，而不是污染 `PATH`。
- `ProjectSnapshot` 覆盖 `src`、`test`、`Cargo.toml`、`Cargo.lock`、`build.rs`。
- `violates_no_fake_impl()` 拒绝 `todo!()`、`unimplemented!()`、placeholder panic 和硬编码 expected output。
- `LogAgent` 将 stdout、stderr、trace、frames、locals 压缩为 `runtime.json`。
- `RuntimeProbeService` 支持 `debug_probe` 和 `static_probe_update`，且 probe 与 edits 互斥。

待补证数据：

- 首次测试通过率、修复后通过率、最终失败用例数。
- 每个 failing case 的材料请求数、编辑数、probe 数和 LLM 轮数。
- snapshot 创建 / 恢复耗时与回归回滚次数。
- LogAgent / probe / 反作弊 / 回归检查消融。

局限：

- 动态 probe 直接运行 binary，不完整重放 shell 脚本。
- release binary 的调试信息可能不足，LLDB locals 质量不稳定。
- 回归检查只覆盖当前 baseline 已通过用例。

### 9. Observability and Reproducibility：可审计运行

本节放在方法末尾或实验设置开头均可。建议强调可观测性是系统设计的一部分，但不要把它包装成核心算法贡献。

可引用系统机制：

- `agent.sh` 主日志：`log/agent-<project>-<timestamp>.log`。
- `rtest_agent.sh` 局部日志：`log/rtest-*.log`。
- `RoundLogger`：`log/round_logs/<run>/<round>-<objective>.md`。
- `translation_metrics.json`：总耗时和 LLM 请求数。
- `repair_journal.jsonl`：编译修复过程记录。
- `.cgr_logs/runtime.json`、`debug_probe_*.json`、`static_probe_*.json`：功能修复证据。

待补证数据：

- 是否新增 run manifest，记录 git commit、命令行、环境变量、模型配置、数据集 hash 和输出目录。
- round log 解析脚本，用于阶段级请求数和 token 统计。

局限：

- round logs 是 Markdown，批量统计需额外解析。
- 日志可能包含源码和敏感内容，公开论文 artifact 前需要脱敏。

## 可引用系统机制

| 机制 | 论文中建议命名 | 主要证据路径 | 论文用途 |
| --- | --- | --- | --- |
| C 预检 | C reference validation gate | `src/agent/main.py`、`CProjectBuilder.clean_and_build` | 排除坏输入项目 |
| tree-sitter 事实抽取 | Static fact extraction | `src/parse/c_ast.py` | 证明系统不是直接全文 prompt |
| 模块切分 | Prompt-budget aware decomposition | `src/agent/split.py` | 支撑上下文压缩贡献 |
| 迁移契约 | Scope contract | `translation_contract.json`、`src/agent/spec_agent.py` | 控制生成边界 |
| 文档 lint | Scope expansion lint | `translation_lint.json` | 度量文档越界 |
| 按需检索 | File-plan-driven evidence retrieval | `ContextualRustAgent`、`RustGenerationSpecAgent` | 主生成贡献 |
| 符号注册表 | Symbol guardrail | `RustProjectRegistry` | 降低重复定义和跨文件猜测 |
| 编译前沿 | Compile frontier acceptance | `RustRepairAgent` | 修复贡献 |
| 错误分批 | Active diagnostic batch | `ErrorOrganizerAgent` | 降低修复 prompt 噪声 |
| shell wrapper | Shell-test preserving runner | `TestRunner` | 保留 C 测试 oracle |
| snapshot 回滚 | Regression-constrained repair | `ProjectSnapshot`、`SuiteRepairCoordinator` | 保证局部修复不破坏已通过用例 |
| LogAgent | Runtime evidence manager | `LogAgent`、`RuntimeProbeService` | 运行时证据增强 |

## 需要补证的数据

方法章节引用前，建议优先补齐以下数据：

| 数据 | 来源 | 用途 |
| --- | --- | --- |
| 数据集规模 | `datasets/`、`src/parse/res/*.json` | 实验设置 |
| 每阶段 LLM 请求数 | round logs objective 前缀 | 成本分析 |
| 每阶段耗时 | 主日志、round logs、后续 run manifest | 成本分析 |
| 初始生成后编译通过率 | `cargo check` / `cargo build --release` | 生成器对比 |
| 修复后编译通过率 | `RustRepairAgent` summary、journal | 编译修复效果 |
| shell 测试通过率 | RustTestAgent summary | 功能正确性 |
| 越界文件 / 依赖数量 | contract lint、Rust lint | scope control 效果 |
| unsafe / raw pointer / C ABI 泄漏 | Rust lint 结果或静态扫描 | Rust 质量指标 |
| probe 使用频次和成功率 | `.cgr_logs/debug_probe_*.json`、`static_probe_*.json` | LogAgent 消融 |
| snapshot 回滚次数 | rtest 日志 | 回归保护收益 |

## 局限

- 当前很多机制是启发式实现，如 registry、contract lint、frontier metrics、ErrorOrganizer 诊断切分和 static probe 表达式渲染。论文应把它们描述为工程化 guardrail，而不是完备静态分析。
- `translation_contract.json` 与 `SpecAgent` 文档体系仍在演进。若实验中使用不同版本，需要固定 commit 和配置。
- 默认入口强制 `--rust-entry-kind main`，对纯库项目需要记录或单独消融。
- 动态 probe 不是完整测试脚本调试，只是对 Rust / C binary 的直接 LLDB 执行。
- 当前日志足够人工审计，但阶段级成本和跨运行统计需要新增结构化提取脚本。
