# C 到 Rust 翻译项目论文调研目录框架

日期：2026-06-04

## 1. 文档目的

本文先完成论文调研的目录框架设计，后续再按子目录分派更细的 agent 调研。调研主线以 `scripts/agent.sh` 当前默认流程为准：

```text
scripts/agent.sh
  -> src/agent/main.py
  -> C 项目预检
  -> SpecAgent 文档化分析
  -> ContextualRustAgent 按需上下文生成
  -> UnfinishedCodeAgent 补全占位
  -> RustRepairAgent 编译修复
  -> RustTestAgent 功能测试与修复
```

早期链路 `CDocAgent -> RustAgent -> CodeFixer -> TestFixer` 作为对照基线保留，不作为论文主线。

## 2. 推荐调研目录

建议后续调研文档统一放在：

```text
doc/paper_research/
  00_index/
    00_research_map.md
    01_glossary.md
    02_claims_and_evidence_matrix.md
  01_pipeline_orchestration/
    01_agent_sh_entry.md
    02_main_py_workflow.md
    03_reproducibility_and_logging.md
  02_c_program_understanding/
    01_tree_sitter_static_analysis.md
    02_module_splitter.md
    03_spec_agent_documents.md
    04_pointer_macro_evidence.md
  03_context_construction/
    01_spec_context_indexing.md
    02_translation_contract.md
    03_prompt_budget_and_material_policy.md
    04_round_logging_and_metrics.md
  04_rust_generation/
    01_contextual_rust_agent.md
    02_file_planning_and_entry_strategy.md
    03_symbol_registry_and_read_requests.md
    04_baseline_generators.md
  05_compile_repair/
    01_rust_repair_agent.md
    02_error_frontier_and_acceptance.md
    03_error_organizer_batches.md
    04_deterministic_structural_repair.md
  06_functional_testing/
    01_rust_test_agent.md
    02_test_runner_and_wrappers.md
    03_snapshot_regression_policy.md
    04_log_agent_runtime_evidence.md
    05_dynamic_and_static_probes.md
  07_metrics_and_evaluation/
    01_dataset_inventory.md
    02_success_metrics.md
    03_ablation_plan.md
    04_failure_taxonomy.md
    05_cost_and_latency.md
  08_engineering_optimizations/
    01_environment_isolation.md
    02_context_deduplication.md
    03_output_contracts_and_parsers.md
    04_safety_guards.md
  09_paper_materials/
    01_method_section_outline.md
    02_system_diagrams.md
    03_tables.md
    04_related_work_mapping.md
```

## 3. 章节级调研框架

### 3.1 `00_index`：总图与论点矩阵

目标是把论文可能主张的贡献点与代码证据绑定起来，避免后续调研只堆实现细节。

需要产出：

- `00_research_map.md`：系统全景图，明确每个 agent 的输入、输出、外部判别器和失败回路。
- `01_glossary.md`：统一术语，如 SpecAgent、translation contract、error frontier、runtime evidence、static probe。
- `02_claims_and_evidence_matrix.md`：论文论点、对应代码路径、可实验验证方式、潜在反例。

重点论点候选：

- 该系统不是单次翻译，而是“静态理解 + 约束生成 + 编译反馈 + 功能测试反馈”的闭环迁移框架。
- 文档不是附属说明，而是下游生成和修复的控制信号。
- 编译器、shell 测试、C 参考程序和 LLDB 探针共同构成外部判别器。

### 3.2 `01_pipeline_orchestration`：入口与复现

调研对象：

- `scripts/agent.sh`
- `src/agent/main.py`
- `scripts/rtest_agent.sh`
- `scripts/run_repair.sh`

工程细节要点：

- `agent.sh` 自动查找 Python：优先 `PYTHON`、`CONDA_PREFIX`、`VIRTUAL_ENV`，默认规避系统 Python，避免依赖污染。
- 默认打开 `--use-rust-repair-agent`、`--use-contextual-rust-agent`、`--use-rust-test-agent`、`--use-spec-agent` 和 `--use-error-organizer-agent`。
- `TEMP/TMP/TMPDIR` 被重定向到 `/tmp/cgrcode-agent`，日志写入 `log/agent-<project>-<timestamp>.log`。
- `CGR_NO_DEFAULT_FLAGS`、`CGR_RUST_REPAIR_MAX_ITERATIONS`、`CGR_RUST_TEST_MAX_ITERATIONS` 等环境变量可作为消融实验控制点。
- `main.py` 先执行 C 项目 clean/build 预检，再进入翻译，保证测试基线不是坏的 C 工程。

调研问题：

- 当前默认参数为什么比早期默认链路更适合论文实验？
- 续跑模式 `--continue` 与 `--freeze-c-docs` 分别适合哪类实验？
- 日志、round logs、translation metrics 能否支持实验复现？

### 3.3 `02_c_program_understanding`：C 项目理解与文档化

调研对象：

- `src/parse/c_ast.py`
- `src/agent/spec_agent.py`
- `src/agent/split.py`
- `src/agent/pointer_agent.py`
- `src/agent/macro_agent.py`
- 既有文档：`doc/split_py_实现解析.md`

核心机制：

- `tree-sitter-c` 负责结构化解析，抽取函数、结构体、宏、include、调用关系等。
- `ModuleSplitter` 使用目录、函数调用、结构体共用、函数名前缀、行数阈值混合拆分模块。
- `SpecAgent` 生成 `repo manifest`、接口文档、行为文档、constitution、模块级 `spec/plan/tasks`。
- `PointerAgent` 和 `MacroAgent` 可生成迁移风险证据；在 `SpecAgent` 路径中更适合作为模块级辅助证据，而不是直接塞满全局 prompt。

工程优化点：

- 路径归一化兼容 Windows/Linux。
- 函数字段做 schema 兼容：`func_defid/func_name/span/num_lines` 被归一为 `name/file/start_line/end_line/line_count`。
- 模块内聚度用 internal/external call ratio 近似衡量，用于解释模块切分质量。
- 大模块继续按共享结构体、函数名前缀、文件局部性切成函数簇，服务 prompt 尺寸控制。

需要验证的技术风险：

- `SpecAgent` 里若干上下文上限当前设置为 `TEMP = 9999999`，实际等于关闭截断；后续需评估不同预算对成功率、请求数和失败类型的影响。
- 启发式模块切分依赖上游调用图质量；同名文件、宏展开和函数指针可能降低准确度。
- Markdown 文档仍有自然语言漂移风险，需要与 `translation_contract.json` 或 `spec_context.json` 的结构化层对照。

### 3.4 `03_context_construction`：上下文构造与预算控制

调研对象：

- `src/agent/spec_json_agent.py`
- `src/agent/alternatives/contextual_spec_agent.py`
- `src/agent/rtest/material_policy.py`
- `src/agent/rtest/source_loader.py`
- `src/utils/round_logger.py`
- `src/utils/translation_metrics.py`

重点问题：

- 哪些上下文是事实源，哪些是 LLM 摘要，哪些是执行反馈？
- 文档索引如何从“全量塞入”转成“按文件、按 query 选择”？
- round log 如何记录每次 LLM 调用的目标、调用栈、耗时、token 估算和返回内容？

工程细节：

- `RoundLogger` 每轮记录 request/reply、objective、backend、call stack、耗时和 token usage。
- `TranslationMetrics` 记录总耗时和 LLM 请求数，输出到 `translation_metrics.json`。
- `RustTestAgent` 使用 `PROMPT_MATERIAL_BUDGET_CHARS = 256000` 级别的字符预算，并通过材料策略避免 stale snippets 污染下一轮。
- `source_loader` 支持 whole-file、line-range、search-results 等模式，适合研究“按需读取”是否优于一次性全文注入。

### 3.5 `04_rust_generation`：Rust 生成策略

调研对象：

- `src/agent/alternatives/contextual_rust_agent.py`
- `src/agent/rust_agent.py`
- `src/agent/alternatives/stable_rust_agent.py`
- `src/agent/alternatives/growth_rust_agent.py`
- `doc/rust_agent_generation_plan.md`
- `doc/stable_rust_agent.md`

主线机制：

- `ContextualRustAgent` 不把所有文档作为静态 prompt，而是维护文档索引、符号注册表和文件计划。
- 入口策略 `--rust-entry-kind main/lib/auto` 控制生成 `src/main.rs` 还是 `src/lib.rs`，避免二者混用。
- 可选证据过滤会在未开启 pointer/macro 时剔除陈旧辅助文档。
- 已生成 Rust 文件以 symbol registry 摘要表示，降低下游文件生成时的上下文体积。

工程优化点：

- 文件列表经过本地清洗，强制包含 `Cargo.toml`、入口文件和 `README.md`，过滤不应生成的测试、example、bench 等路径。
- translation contract 和 source records 可作为更硬的项目边界，约束模型不要生成 C 项目不存在的高级功能。
- `StableRustAgent`、`GrowthRustAgent` 可作为消融基线：薄 prompt 稳定生成、生长式最小可编译集、上下文式按需读取三种策略可比较。

### 3.6 `05_compile_repair`：编译修复闭环

调研对象：

- `src/agent/rust_repair_agent.py`
- `src/agent/error_organizer_agent.py`
- `src/agent/rust_structural_repair/`
- 既有测试临时产物中的 `repair_journal.jsonl`

核心机制：

- `RustRepairAgent` 每轮复制项目副本，支持 in-place 修复，也能只在候选结果更好时推进基线。
- 修复前先执行确定性结构修复，例如括号、局部语法结构清洗，把简单错误从 LLM 负担中剥离。
- `error_signature` 和 `frontier_metrics` 用于判断候选是否推进了编译前沿。
- `ErrorOrganizerAgent` 按错误批次组织诊断，避免一次 prompt 淹没在大量编译错误中。
- `repair_journal.jsonl` 记录 clone、baseline、LLM repair、应用记录、错误签名、接受原因等，可作为论文中的过程证据。

需要细化的研究问题：

- “错误数减少”与“暴露更深层错误”如何定义和比较？
- 只接受推进前沿的策略是否能显著减少越修越坏？
- 错误分批对 token 成本、修复轮数、最终编译通过率的影响是什么？

### 3.7 `06_functional_testing`：功能测试与运行证据

调研对象：

- `src/agent/rtest/rust_test_agent.py`
- `src/agent/rtest/test_runner.py`
- `src/agent/rtest/repair_prompt.py`
- `src/agent/rtest/snapshot.py`
- `src/agent/rtest/log_agent.py`
- `src/agent/rtest/runtime_probe.py`
- `src/agent/rtest/debug_backends.py`
- 既有文档：`doc/log_agent_rtest_repair_report.md`、`doc/dynamic_instrumentation_report.md`

核心机制：

- `RustTestAgent` 构建 C 参考二进制，复制测试目录，并把测试脚本作为只读基准。
- `TestRunner` 通过 wrapper、`C_BIN/RUST_BIN` 映射和临时运行目录适配原始 shell 测试。
- 每个失败用例进入 LLM 修复循环，修复前创建 snapshot；若回归其他用例，则恢复快照。
- prompt 要求 JSON 输出，区分 edits、material requests、debug probe、static probe。
- LogAgent 将 stdout/stderr/bash trace/runtime evidence 压缩成下一轮可用证据。
- 动态探针通过 LLDB 采集 locals、backtrace、watch expression；静态插桩只作用于临时副本，不污染项目。

工程级保护：

- 测试脚本不可修改，`--translate-tests` 仅作兼容参数。
- 禁止 fake 实现，使用 `signals.py` 检测绕过测试、硬编码输出等风险。
- debug probe 不能与 edits 同轮执行，避免“改了代码又采证据”导致证据时序混乱。
- 限制 debug probe 次数，重复探针会被过滤。
- 旧测试产物和 stale snippets 会被清理，避免下一轮依据过期材料修复。

### 3.8 `07_metrics_and_evaluation`：指标和实验设计

建议指标：

- 编译通过率：`cargo check`、`cargo build --release`、`cargo test`。
- 功能通过率：C 项目 shell 测试迁移后的 pass/fail 数。
- 修复效率：RustRepairAgent 迭代数、RustTestAgent 每失败用例迭代数、最终 accepted frontier 次数。
- 成本：LLM 请求数、总耗时、round log token 估算、平均 prompt 字符数。
- 代码质量：unsafe 行数、裸指针类型/解引用次数、依赖数量、生成文件数量、测试脚本是否被修改。
- 行为一致性：stdout/stderr 差异、exit code 差异、文件系统副作用差异。

建议消融：

- `SpecAgent` vs `CDocAgent`。
- `ContextualRustAgent` vs `RustAgent` vs `StableRustAgent` vs `GrowthRustAgent`。
- 开启/关闭 `ErrorOrganizerAgent`。
- 开启/关闭 `RustRepairAgent`，对比 legacy `CodeFixer`。
- 开启/关闭 `RustTestAgent`。
- 开启/关闭 `LogAgent` 动态/静态探针。
- 不同 prompt budget、不同 repair iteration 上限。
- 开启/关闭 pointer/macro 辅助证据。

数据集目录：

- `datasets/`：C 项目输入。
- `output/`、`output_deepseek/`、`output_gpt*`：不同模型或配置产物。
- `src/parse/res/*.json`：源码结构化记录。
- `log/`：主流程日志、round logs。

### 3.9 `08_engineering_optimizations`：工程优化专题

本目录不按 agent 分，而按工程问题组织，适合论文“系统实现细节”或附录。

建议专题：

- 环境隔离：Python runner 选择、依赖检查、临时目录、系统 Python 防护。
- 上下文去重：文档唯一事实源、Markdown 去重、材料预算、stale snippet 清理。
- 输出契约：XML-like 标签解析、JSON repair contract、本地 sanitizer。
- 路径与平台：Windows/Linux 路径归一、POSIX shell 测试适配。
- 运行安全：只读测试、临时项目副本、snapshot 回滚、静态插桩不污染源码。
- 模型鲁棒性：API 重试、流式响应诊断、`max_tokens` 自适应降低、round log 记录失败。

## 4. 分 agent 调研任务设计

后续建议按以下“分子 agent”并行调研，每个 agent 输出 1 份 Markdown，最后汇总到 `00_index/02_claims_and_evidence_matrix.md`。

| 调研 agent | 范围 | 主要代码 | 交付物 |
| --- | --- | --- | --- |
| A0 编排 agent | 入口、参数、复现、日志 | `scripts/agent.sh`、`src/agent/main.py` | `01_pipeline_orchestration/*.md` |
| A1 C 理解 agent | C 静态分析、模块切分、spec 文档 | `parse/c_ast.py`、`spec_agent.py`、`split.py` | `02_c_program_understanding/*.md` |
| A2 上下文 agent | 文档索引、材料预算、round log、metrics | `contextual_spec_agent.py`、`round_logger.py`、`translation_metrics.py` | `03_context_construction/*.md` |
| A3 生成 agent | Rust 文件规划、符号表、入口策略 | `contextual_rust_agent.py`、`rust_agent.py` | `04_rust_generation/*.md` |
| A4 编译修复 agent | 编译错误、frontier、错误分批、结构修复 | `rust_repair_agent.py`、`error_organizer_agent.py` | `05_compile_repair/*.md` |
| A5 测试修复 agent | shell 测试、snapshot、回归保护 | `rtest/rust_test_agent.py`、`test_runner.py`、`snapshot.py` | `06_functional_testing/*.md` |
| A6 运行证据 agent | LogAgent、动态探针、静态插桩 | `log_agent.py`、`runtime_probe.py`、`debug_backends.py` | `06_functional_testing/04-05*.md` |
| A7 实验 agent | 数据集、指标、消融、失败分类 | `scripts/*.sh`、`output*/`、`log/` | `07_metrics_and_evaluation/*.md` |
| A8 工程优化 agent | 环境、输出契约、安全防护 | 跨模块 | `08_engineering_optimizations/*.md` |

每个调研 agent 的文档应包含：

- 研究问题：该模块解决什么论文问题。
- 流程图：输入、处理、输出、失败回路。
- 关键技术点：至少列 5 个工程细节。
- 可引用代码证据：文件路径、函数名、核心字段。
- 实验钩子：哪些参数、日志或产物能验证该机制。
- 局限与反例：该机制什么时候会失败。
- 可写入论文的位置：方法、实验、消融、讨论或附录。

## 5. 论文技术贡献候选

### 5.1 多阶段外部认知支架

系统先用静态分析和文档生成把 C 工程压缩成结构化迁移上下文，再让 LLM 生成 Rust。重点不在单个模型能力，而在外部 harness 如何降低模型一次性理解大型 C 工程的难度。

### 5.2 按需上下文生成

`ContextualRustAgent` 使用文档索引、源码记录和 Rust 符号注册表，让模型按文件生成时只看到相关上下文，并可通过显式读取请求补充材料。

### 5.3 编译前沿驱动的修复

`RustRepairAgent` 把 `cargo check/test/build` 反馈转成错误签名和 frontier metrics，只在修复结果推进前沿时接受候选，降低退化风险。

### 5.4 C 参考行为驱动的功能修复

`RustTestAgent` 不是简单运行 `cargo test`，而是以 C 参考二进制和原始 shell 测试作为 oracle，通过 wrapper、trace、runtime evidence 和 snapshot 回滚实现行为修复闭环。

### 5.5 运行时证据增强

LogAgent 与 probe 机制让 LLM 可以主动请求运行时证据，而不是只依赖失败输出。这适合表述为“LLM-guided evidence acquisition”。

## 6. 第一轮调研优先级

建议第一轮先做 6 份文档：

1. `01_pipeline_orchestration/02_main_py_workflow.md`
2. `02_c_program_understanding/02_module_splitter.md`
3. `02_c_program_understanding/03_spec_agent_documents.md`
4. `04_rust_generation/01_contextual_rust_agent.md`
5. `05_compile_repair/01_rust_repair_agent.md`
6. `06_functional_testing/01_rust_test_agent.md`

这 6 份覆盖从 C 理解到 Rust 生成、编译修复、功能测试的主链路，足够支撑论文方法章节初稿。第二轮再补 LogAgent、指标体系、消融和工程优化专题。

## 7. 当前需要特别记录的开放问题

- `SpecAgent` 的上下文裁剪常量目前等于超大值，是否会让“上下文压缩”贡献在实际默认配置中被削弱？
- `PointerAgent` / `MacroAgent` 默认只通过环境变量开启，论文实验是否需要固定打开？
- `output/`、`output_deepseek/`、`output_gpt*` 的差异需要建立统一命名和元数据记录，否则难以复现实验。
- 现有指标只记录总耗时和 LLM 请求数，若论文需要成本分析，应补充每阶段请求数、prompt 字符数、token、修复迭代数。
- 裸指针、unsafe、依赖数量、生成文件数量等代码质量指标需要统一脚本输出格式。

## 8. 与已有文档的关系

可直接复用的已有材料：

- `src/README.md`：项目总览，可作为 `00_index` 的基础。
- `doc/split_py_实现解析.md`：ModuleSplitter 深入解析。
- `doc/c_docs_rust_agent_optimization_20260420.md`：文档去重、translation contract、Rust 生成边界。
- `doc/log_agent_rtest_repair_report.md`：LogAgent 与 rtest 修复机制。
- `doc/dynamic_instrumentation_report.md`：动态插桩机制。
- `doc/rust_agent_generation_plan.md`：早期 RustAgent 文件生成计划。
- `doc/stable_rust_agent.md`：StableRustAgent 基线说明。

新调研文档不应重复这些报告的全部内容，而应把它们拆成论文可引用的证据块，并补充代码路径、实验钩子和局限分析。
