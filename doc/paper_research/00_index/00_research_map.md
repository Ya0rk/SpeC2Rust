# 研究地图

## 研究主线

本项目的论文调研以“C 到 Rust 的闭环翻译系统”为主线。系统不把 LLM 当作一次性代码翻译器，而是把 LLM 放在由静态分析、文档约束、编译器反馈、C 参考测试和运行时证据构成的工程 harness 中。

默认实验入口来自 `scripts/agent.sh`，其默认参数打开：

- `--use-spec-agent`
- `--use-contextual-rust-agent`
- `--use-rust-repair-agent`
- `--use-rust-test-agent`
- `--use-error-organizer-agent`

因此论文主线应围绕当前默认链路，而不是早期的 `CDocAgent -> RustAgent -> CodeFixer -> TestFixer`。

## 系统阶段

| 阶段 | 核心问题 | 核心模块 | 主要产物 |
| --- | --- | --- | --- |
| 入口编排 | 如何稳定复现实验流程 | `scripts/agent.sh`、`src/agent/main.py` | 主流程日志、输出目录、指标文件 |
| C 项目理解 | 如何把 C 工程压缩为可迁移事实 | `SpecAgent`、`ModuleSplitter`、`CCodeAnalyzer` | `c_docs/`、模块 spec、接口/行为文档 |
| 上下文构造 | 如何避免全量上下文淹没模型 | `ContextualSpecAgent`、`SpecJsonAgent`、`RoundLogger` | 文档索引、上下文切片、round logs |
| Rust 生成 | 如何按文件、按符号生成 Rust 工程 | `ContextualRustAgent` | `Cargo.toml`、`src/*.rs`、符号注册表 |
| 编译修复 | 如何用编译器反馈推进修复 | `RustRepairAgent`、`ErrorOrganizerAgent` | `repair_journal.jsonl`、修复后的 Rust 工程 |
| 功能测试 | 如何用 C 行为作为 oracle | `RustTestAgent`、`TestRunner` | shell 测试结果、trace、runtime evidence |
| 运行证据 | 如何让模型主动采证 | `LogAgent`、`RuntimeProbeService` | debug/static probe 证据 |
| 指标评估 | 如何量化成功率、成本与质量 | 脚本与日志产物 | 成功率、迭代数、请求数、unsafe/raw pointer 指标 |

## 子目录导航

- `01_pipeline_orchestration/`：入口脚本、主流程和复现机制。
- `02_c_program_understanding/`：C 静态分析、模块切分、文档生成和 pointer/macro 证据。
- `03_context_construction/`：上下文索引、材料预算、round log 和指标。
- `04_rust_generation/`：上下文式 Rust 生成、文件计划、符号注册表和基线生成器。
- `05_compile_repair/`：编译修复、错误前沿、错误分批和确定性结构修复。
- `06_functional_testing/`：功能测试、wrapper、snapshot、LogAgent 和 probe。
- `07_metrics_and_evaluation/`：数据集、指标、消融、失败分类和成本分析。
- `08_engineering_optimizations/`：环境隔离、上下文去重、输出契约和安全防护。
- `09_paper_materials/`：方法章节、图表、表格和相关工作映射。

## 论文贡献候选

1. 多阶段外部认知支架：用静态分析和文档化流程降低 LLM 理解 C 工程的负担。
2. 按需上下文生成：从全量文档注入转向按文件、按符号、按 query 取材。
3. 编译前沿驱动修复：用错误签名和 frontier metrics 控制修复基线推进。
4. C 参考行为驱动功能修复：用原始 shell 测试和 C 可执行程序作为行为 oracle。
5. 运行时证据增强：允许 LLM 通过动态/静态探针主动获取诊断证据。

## 后续汇总规则

每份子文档都应回填到 `02_claims_and_evidence_matrix.md`：

- 若发现可支撑论文贡献的机制，添加到“正向证据”。
- 若发现实现不稳、默认配置不一致或实验风险，添加到“局限与反例”。
- 若发现可消融的参数或开关，添加到“实验钩子”。
