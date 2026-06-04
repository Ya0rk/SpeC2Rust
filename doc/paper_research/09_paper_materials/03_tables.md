# 论文表格草案

## 用途

本文整理论文可直接使用或稍作改写的表格草案。表格目标是把系统机制、证据路径、实验变量和局限压缩成论文可读材料，避免正文反复堆代码路径。

## 内容结构

建议正文使用 5 类表格：

1. 系统阶段表。
2. 核心机制和证据表。
3. 消融变量表。
4. 评价指标表。
5. 局限与威胁表。

其余表格可放入附录。

## 可引用系统机制

本文表格覆盖的主要可引用机制包括：

- `scripts/agent.sh` 默认主线：`SpecAgent -> ContextualRustAgent -> RustRepairAgent -> RustTestAgent`。
- `SpecAgent` 的 tree-sitter 静态事实、`ModuleSplitter`、`translation_contract.json` 和 `translation_lint.json`。
- `ContextualRustAgent` 的 file plan、`<CGR_READ>`、registry summary、contract lint 和本地 `Cargo.toml` / `src/lib.rs` 兜底。
- `RustRepairAgent` 的诊断 / 编辑分离、结构化编辑、ErrorOrganizer active batch、frontier metrics 和 `repair_journal.jsonl`。
- `RustTestAgent` 的只读 shell tests、wrapper、snapshot 回滚、baseline 回归检查、反作弊过滤、LogAgent 和 probe evidence。
- `RoundLogger` 与 `translation_metrics.json` 的 LLM 调用审计和总成本记录。

## 表 1：端到端阶段和产物

| 阶段 | 输入 | 主要模块 | 输出产物 | 外部判别器 | 论文用途 |
| --- | --- | --- | --- | --- | --- |
| 入口编排 | 项目名、环境变量、模型配置 | `scripts/agent.sh`、`main.py` | 主日志、输出目录、默认 flags | Python 依赖检查 | 实验复现 |
| C 预检 | C 项目源码和构建文件 | `CProjectBuilder` | 可构建 C 参考项目 | `make clean/build` | 排除坏输入 |
| C 静态理解 | `.c` / `.h` | `CCodeAnalyzer` | `project_analysis`、`src/parse/res/*.json` | tree-sitter | 程序事实抽取 |
| 模块切分 | 静态事实、调用图、结构体使用 | `ModuleSplitter` | `module_units`、`cluster_units` | 内聚度和规模阈值 | prompt 预算控制 |
| 文档化约束 | 模块事实 | `SpecAgent` | `c_docs`、`translation_contract.json`、`translation_lint.json` | 文档 lint | 迁移范围控制 |
| Rust 生成 | `c_docs`、source JSON、contract | `ContextualRustAgent` | Rust crate、`.cgr_generation_plan.json`、`.cgr_api_contract.json` | contract lint、registry lint | 按需上下文生成 |
| 编译修复 | Rust crate、C/spec 证据 | `RustRepairAgent`、`ErrorOrganizerAgent` | 修复后 Rust crate、`repair_journal.jsonl` | `cargo check`、`cargo build --release` | 编译前沿修复 |
| 功能测试修复 | Rust release binary、C binary、shell tests | `RustTestAgent`、`TestRunner` | 测试结果、runtime evidence、probe evidence | 原始 shell 测试、C 参考行为 | 行为等价验证 |
| 可观测性 | LLM 请求、运行日志 | `RoundLogger`、`TranslationMetrics` | round logs、`translation_metrics.json` | 时间、token usage 或估算 | 成本和审计 |

可引用系统机制：

- 默认入口把上述阶段串成一条主线，而不是人工逐步运行。
- 每个后期阶段都有独立持久化证据，便于复盘失败原因。

需要补证的数据：

- 每阶段执行成功率、跳过率和失败原因。
- 每阶段平均耗时、LLM 请求数和 prompt token。

局限：

- 表中外部判别器不等价于完整语义证明；shell 测试只覆盖测试集中可观察行为。

## 表 2：核心贡献与实现证据

| 贡献候选 | 系统机制 | 主要代码 / 产物 | 可实验验证 | 当前证据等级 |
| --- | --- | --- | --- | --- |
| 多阶段外部认知支架 | C 预检、静态事实、文档化约束、Rust 生成、编译修复、功能测试 | `scripts/agent.sh`、`src/agent/main.py` | 默认链路 vs 单次生成 | B |
| 程序事实驱动的 C 理解 | tree-sitter 函数 / 调用 / 宏 / 结构体抽取 | `src/parse/c_ast.py`、`src/parse/res/*.json` | 抽取覆盖率、span 准确率 | B |
| 面向 prompt 预算的模块切分 | 目录、调用图、结构体共用、前缀和规模阈值 | `src/agent/split.py` | 切分信号消融 | B |
| 迁移范围契约 | `translation_contract.json` 控制文件、依赖、FFI 和禁止能力 | `src/agent/spec_agent.py`、`translation_contract.json` | contract on/off 越界率 | B |
| 按需上下文生成 | file plan、spec section 检索、`<CGR_READ>` | `ContextualRustAgent`、`RustGenerationSpecAgent` | 全文 prompt vs 按需检索 | B |
| 符号护栏 | registry 防重复定义、跨文件私有访问和未规划模块引用 | `RustProjectRegistry`、`.cgr_api_contract.json` | registry on/off 引用错误率 | B |
| 编译前沿修复 | blocker-aware frontier acceptance | `RustRepairAgent`、`repair_journal.jsonl` | frontier vs error-count | B |
| 错误分批 | rustc 诊断按错误码和主文件聚类 | `ErrorOrganizerAgent` | batch size 消融 | B |
| C shell tests 行为 oracle | wrapper 保留原始测试脚本 | `RustTestAgent`、`TestRunner` | 功能通过率 | B |
| 主动运行时取证 | `debug_probe`、`static_probe_update` | `LogAgent`、`RuntimeProbeService` | LogAgent/probe 消融 | B |

证据等级说明：

| 等级 | 定义 |
| --- | --- |
| A | 有代码路径、日志产物和系统实验数据。 |
| B | 有代码路径和可观察产物，缺少完整系统实验。 |
| C | 主要是设计意图或文档描述，需要进一步验证。 |

需要补证的数据：

- 将所有贡献候选提升到 A 级所需的实验表。
- 每个机制的失败反例和负面结果。

局限：

- 当前大多数条目是 B 级证据，论文结果章节必须补真实实验。

## 表 3：默认配置和消融开关

| 变量 | 默认主线 | 控制方式 | 观测指标 | 注意事项 |
| --- | --- | --- | --- | --- |
| C 文档路径 | `SpecAgent` | `--use-spec-agent` | 文档大小、越界率、生成成功率 | baseline 需用 `CGR_NO_DEFAULT_FLAGS=1` |
| Rust 生成器 | `ContextualRustAgent` | `--use-contextual-rust-agent`、`--use-stable-rust-agent`、`--use-growth-rust-agent` | 初始编译率、prompt token、越界文件数 | 三者互斥 |
| Rust 入口 | `main` | `--rust-entry-kind auto/main/lib` | `main.rs` / `lib.rs` 混用错误 | 默认对 library 可能偏置 |
| 编译修复 | 开启 | `--use-rust-repair-agent` | `cargo check` / release build 通过率 | 与 legacy `CodeFixer` 对比 |
| 错误分批 | 开启，batch size 10 | `--use-error-organizer-agent --error-batch-size N` | 修复轮数、prompt 长度、停滞率 | 第 1 批未必总是根因 |
| 功能测试修复 | 开启 | `--use-rust-test-agent` | shell 测试通过率 | 进入前需 release build 通过 |
| 编译修复轮数 | 64 | `CGR_RUST_REPAIR_MAX_ITERATIONS`、`--rust-repair-max-iterations` | 通过率、成本 | 高预算可能掩盖生成差异 |
| 测试修复轮数 | 64 | `CGR_RUST_TEST_MAX_ITERATIONS`、`--rust-test-agent-max-iterations` | 功能通过率、成本 | JSON 协议失败也会消耗轮次 |
| 测试 prompt 预算 | 256000 字符 | `CGR_RUST_TEST_PROMPT_BUDGET_CHARS` | 材料淘汰、修复成功率 | 字符预算不等价 token |
| LogAgent | 默认关闭，可选 | `CGR_USE_LOG_AGENT=1`、`--use-log-agent` | probe 次数、修复轮数 | LLDB 环境敏感 |
| PointerAgent | 默认关闭，可选 | `CGR_USE_POINTER_AGENT=1` | 所有权错误、裸指针错误 | 启发式扫描有误报 |
| MacroAgent | 默认关闭，可选 | `CGR_USE_MACRO_AGENT=1` | 宏相关错误、条件编译错误 | 不执行 preprocessor |

可引用系统机制：

- 默认主线足以作为论文主实验配置。
- 可选证据层以环境变量或 CLI 开关启用，适合做消融。

需要补证的数据：

- 每个开关对应的完整运行命令。
- 不同模型 / 输出目录隔离策略。

局限：

- 部分机制没有现成 CLI 级完全关闭开关，如 registry、frontier 策略和 deterministic structural repair。

## 表 4：Rust 生成器 baseline 对比

| 生成器 | 上下文策略 | 文件计划 | 已生成代码上下文 | 本地边界控制 | 适合的论文角色 |
| --- | --- | --- | --- | --- | --- |
| `RustAgent` | 递归读取文档，整体拼接 | LLM 规划后本地过滤 | 后续 prompt 可包含已生成文件内容 | contract lint、API contract、缺失文件补生成 | 功能完整 baseline |
| `StableRustAgent` | 文档按类型裁剪 | LLM 输出 JSON 文件数组 | 每文件前 5000 字符摘要 | 本地 `Cargo.toml` 和 `lib.rs` | 低复杂度 baseline |
| `GrowthRustAgent` | 继承 `RustAgent` | trunk / branch 生长计划 | 增量生成 | 生成中频繁 `cargo check` | 增量编译 baseline |
| `ContextualRustAgent` | 按文件检索 spec/source/registry | contract-first，file-plan-driven | registry summary，不拼全文 | contract lint、registry lint、local Cargo/lib | 论文主方法 |

可引用系统机制：

- 四路生成器由 `main.py` 互斥选择。
- `ContextualRustAgent` 的关键差异是“索引 + 按需读取 + 符号护栏”。

需要补证的数据：

- 初始生成后 `cargo check` 通过率。
- 初始越界文件数、重复定义数、未授权依赖数。
- 修复后最终通过率，避免只看初始状态。

局限：

- baseline 机制差异较大，比较最终通过率时必须同时报告成本和中间状态。

## 表 5：编译修复指标草案

| 指标 | 定义 | 来源 | 说明 |
| --- | --- | --- | --- |
| `check_pass_rate` | `cargo check` 最终通过项目比例 | RustRepairAgent summary 或重跑命令 | 编译修复主指标 |
| `release_build_pass_rate` | `cargo build --release` 最终通过项目比例 | `main.py` gate、repair result | RustTestAgent 进入条件 |
| `repair_iterations` | RustRepairAgent 外层迭代数 | `repair_journal.jsonl` | 反映轮数成本 |
| `llm_edit_count` | 结构化编辑请求次数 | `repair_journal.jsonl` stage | 反映模型修改次数 |
| `cargo_check_count` | 运行 `cargo check` 次数 | journal 或日志解析 | 反映工具成本 |
| `accepted_frontier_count` | 被前沿机制接受的迭代次数 | `iteration_result.accepted_as_best` | 验证 frontier 贡献 |
| `accept_reason_distribution` | 接受原因分布 | `iteration_result.accept_reason` | 区分语法 / 接口 / 签名变化 |
| `syntax_blocker_delta` | syntax blockers 变化 | `frontier_metrics` | 衡量结构性修复收益 |
| `error_batch_switches` | active batch 切换次数 | `organized_error_batch_switched` | 衡量错误分批推进 |
| `deterministic_repair_count` | 本地括号修复触发次数 | 需新增记录 | 当前只打印，需补日志 |

可引用系统机制：

- 前沿机制允许“错误数不降但语法阻塞清除”被视为进展。
- ErrorOrganizer 将长 rustc stderr 转成 active batch。

需要补证的数据：

- 结构修复事件需要写入 journal，否则只能从 stdout 文本粗略解析。
- `error_signature` 需改稳定哈希后再跨运行聚合。

局限：

- `RepairRunResult.test_passed` 在当前语境中表示 release build 结果，不应写成功能测试通过。

## 表 6：功能测试修复指标草案

| 指标 | 定义 | 来源 | 说明 |
| --- | --- | --- | --- |
| `initial_shell_pass_rate` | RustTestAgent 首次 run_all 通过率 | rtest summary | 初始行为质量 |
| `final_shell_pass_rate` | 修复后 shell 测试通过率 | rtest summary | 功能修复主指标 |
| `failed_case_repair_rate` | 失败用例被修复比例 | SuiteRepairCoordinator 结果 | 单用例修复能力 |
| `repair_rounds_per_case` | 每失败用例 LLM 轮数 | rtest 日志 / round logs | 成本 |
| `material_request_count` | C/Rust/test artifact 请求数 | LLM JSON / rtest history | 证据需求 |
| `edit_count` | 通过过滤并应用的 edit 数 | rtest 日志 | 修改强度 |
| `fake_impl_rejection_count` | 假实现或硬编码输出拒绝次数 | rtest 日志 | 评估防护作用 |
| `regression_count` | 当前用例通过后破坏 baseline 的次数 | `_check_regression` 日志 | 回归保护作用 |
| `snapshot_restore_count` | 快照恢复次数 | snapshot / rtest 日志 | 回滚成本和收益 |
| `debug_probe_count` | 动态 probe 次数 | `.cgr_logs/debug_probe_*.json` | LogAgent 使用情况 |
| `static_probe_count` | 静态 probe 次数 | `.cgr_logs/static_probe_*.json` | 静态插桩使用情况 |
| `probe_failure_type` | LLDB 不可用、断点未命中、表达式错误等 | probe JSON | 运行证据质量 |

可引用系统机制：

- RustTestAgent 接受 edit 的条件是 build 通过、当前用例通过且 baseline pass cases 不回归。
- probe 与 edits 互斥，保证证据时序清晰。

需要补证的数据：

- rtest summary 是否已有统一 JSON 输出；若没有，需要从日志抽取或补结构化 summary。
- probe failure type 当前可能需要二次解析 stdout / stderr。

局限：

- shell 测试覆盖率不等于完整行为等价。
- 回归检查只覆盖 baseline pass cases。

## 表 7：运行证据类型

| 证据类型 | 生成位置 | 内容 | 进入下一轮 prompt 的方式 | 风险 |
| --- | --- | --- | --- | --- |
| stdout / stderr tail | `TestRunner.run_single()` | 测试输出尾部 | 普通失败上下文或 `runtime.json` | 关键错误可能在开头被裁剪 |
| bash trace | `capture_trace_for()` | `bash -x` 轨迹 | 懒加载后进入 prompt | 首次 run_all 默认不抓 trace |
| runtime bundle | `.cgr_logs/runtime.json` | case、exit、stdout、stderr、trace、metadata | LogAgent runtime block | run dir 清理后不可恢复 |
| dynamic probe | `debug_probe_*.json` | LLDB frames、locals、watch values | 最近 4 个 probe 摘要 | release binary debug info 不稳定 |
| static probe | `static_probe_*.json` | `[CGR_STATIC:<id>]` 日志行 | 最近 1 个 static probe 摘要 | 临时构建开销大，表达式可能非法 |
| test artifacts | run dir / copied test dir | `.out`、`.err`、`.log`、generated source | `test_artifact_read` | 索引不完整会漏关键中间产物 |
| C source material | C project | 函数或文件片段 | `cgr_read` | 大范围请求会挤占预算 |
| Rust source material | Rust project | whole file 或 line range | `rust_read_requests` | stale snippet 需及时刷新 |

可引用系统机制：

- `MaterialBudget` 管理 C、Rust、测试产物三类材料。
- `RuntimeProbeService.read_runtime_evidence()` 自动合并 runtime 和 probe evidence。

需要补证的数据：

- 各证据类型在成功修复用例中的出现频次。
- 不同证据组合对修复轮数的影响。

局限：

- 证据多不必然提高修复率，可能造成 prompt 噪声。

## 表 8：失败类型和归因草案

| 失败类型 | 可能阶段 | 典型信号 | 可归因机制 | 可改进方向 |
| --- | --- | --- | --- | --- |
| C 项目不可构建 | C 预检 | clean/build 失败 | 输入 oracle 不成立 | 数据集过滤或记录为无效样本 |
| 结构体 / typedef 抽取错误 | C 理解 | `anonymous`、字段缺失 | tree-sitter + typedef 恢复不足 | 强化声明解析 |
| 模块切分过粗 | C 理解 | 文档过长、LLM 漏实现 | `ModuleSplitter` 阈值 | 阈值消融和人工评估 |
| 文档越界 | 文档生成 | `serde`、FFI、Phase 8+ | prompt 和 lint 不足 | contract-first 文档 |
| 文件边界错误 | Rust 生成 | 多生成 / 少生成文件 | entry strategy、allowed files | contract lint 阻断 |
| 重复定义 | Rust 生成 | duplicate type/function | registry 缺失或误检 | registry 消融和 AST 化 |
| unresolved import | Rust 生成 / 编译修复 | rustc import 错误 | 文件计划依赖错误 | 依赖排序和 registry reference |
| borrow checker 错误 | 编译修复 | lifetime / borrow errors | 所有权迁移不足 | PointerAgent 和 C source evidence |
| shell wrapper 失败 | 功能测试 | 命令找不到、PATH 差异 | TestRunner 适配不足 | wrapper / fixture 改进 |
| 行为输出不一致 | 功能测试 | stdout diff、exit code diff | Rust 逻辑偏差 | C source / runtime evidence |
| 回归 | 功能测试 | baseline pass case 失败 | 局部修复破坏共享语义 | snapshot 和 regression prompt |
| probe 无效 | LogAgent | 断点未命中、locals 为空 | LLDB / release debug info | probe build profile |

可引用系统机制：

- 失败分类可直接连接到实验章节的 error analysis。

需要补证的数据：

- 每个失败项目 / 用例的人工标注主因。
- 自动日志信号与人工主因的一致性。

局限：

- 多阶段系统中失败常有级联效应，单一归因可能过度简化。

## 表 9：与 legacy 链路的对照

| 维度 | 当前默认主线 | legacy / baseline 路径 | 论文表达 |
| --- | --- | --- | --- |
| C 理解 | `SpecAgent`、模块级 spec/plan/tasks、contract | `CDocAgent` 生成较粗文档 | 当前主线更强调结构化约束 |
| Rust 生成 | `ContextualRustAgent` 按需检索和 registry | `RustAgent` 全文文档和已生成上下文 | 按需上下文减少漂移 |
| 编译修复 | `RustRepairAgent` 诊断 / 编辑分离和前沿验收 | `CodeFixer` fmt/check/build 三阶段 | 新路径更可审计 |
| 功能修复 | `RustTestAgent` + shell wrapper + snapshot | `TestFixer` 或无功能修复 | C 参考行为作为 oracle |
| 运行证据 | 可选 `LogAgent`、debug/static probe | 主要 stdout/stderr | 主动取证增强 |
| 日志 | round logs、metrics、journal、runtime evidence | 粗粒度日志 | 更适合论文复盘 |

需要补证的数据：

- 同一数据集上完整跑当前主线和 legacy 链路。
- 分别报告生成后、编译修复后、功能修复后的中间状态。

局限：

- legacy 路径可能缺少部分新防护，比较时应说明不是单一机制差异。

## 表 10：局限与威胁有效性

| 类别 | 局限 | 可能影响 | 缓解或论文写法 |
| --- | --- | --- | --- |
| 输入有效性 | C 项目必须可构建 | 无法评价坏输入项目 | C 预检过滤并报告数量 |
| 测试覆盖 | shell tests 不完备 | 通过测试不代表完全等价 | 报告为测试集行为等价 |
| 上下文预算 | 字符预算不是 token 预算 | 成本估算不精确 | round logs 同时给真实 usage 或估算 |
| 静态分析 | 不执行完整 preprocessor | 宏和条件编译漏检 | MacroAgent 消融和反例分析 |
| Rust lint | registry / lint 是正则启发式 | 漏检复杂 Rust 语法 | 描述为 guardrail，不是证明器 |
| 编译前沿 | 默认原地修复回滚不足 | 候选退化可能留在工作树 | copy-runs 作为对照或讨论 |
| 动态 probe | 不重放完整 shell 脚本 | 证据可能与真实失败路径不一致 | 先用 trace / artifacts 提取参数 |
| 复现 | 缺少 run manifest | 多模型实验难复跑 | 补 git commit、配置、数据集 hash |
| 隐私 | round logs 保存源码和 prompt | artifact 发布需脱敏 | 公开统计或脱敏样本 |

可引用系统机制：

- 威胁表适合论文讨论章节，避免过度宣称系统完备性。

需要补证的数据：

- 每类局限在数据集中的出现频次。
- 哪些局限导致最终失败。

局限：

- 本表仍是预注册风险清单，需结合实验结果更新排序。

## 需要补证的数据

| 优先级 | 数据 | 最小实现方式 |
| --- | --- | --- |
| P0 | 项目级最终编译 / 测试状态 | 统一运行脚本输出 JSON summary |
| P0 | round logs 阶段级请求数 | 按 objective 前缀解析 |
| P0 | `repair_journal.jsonl` 前沿统计 | JSONL 聚合 `iteration_result` |
| P0 | RustTestAgent 测试 summary | 若无 JSON，先解析 rtest 日志 |
| P1 | contract / lint 越界统计 | 扫描 `translation_lint.json` 和 Rust files |
| P1 | unsafe / raw pointer / FFI 泄漏 | 静态 grep 或 Rust lint 输出 |
| P1 | probe 使用和失败类型 | 扫描 `.cgr_logs` JSON |
| P2 | C fact extraction precision / recall | 抽样人工标注 |
| P2 | 模块切分人工一致性 | 抽样人工评估 |

## 局限

- 表格里的“证据等级”尚未经过系统实验刷新。
- 部分指标需要新增或统一 JSON 输出，否则会依赖不稳定日志解析。
- 表格没有列具体数值，后续实验完成后应把草案转换为结果表和附录统计表。
