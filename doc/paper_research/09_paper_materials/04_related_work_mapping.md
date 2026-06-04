# 相关工作映射草案

## 用途

本文不是最终相关工作综述，也不包含联网检索得到的正式引用条目。它的用途是把本系统的机制映射到后续应检索和对比的研究主题，帮助论文写作时明确“我们和哪类工作比较，贡献落在哪里，哪些地方不能夸大”。

由于本轮要求不要联网，本文只给出主题、检索关键词、对比维度和本系统可引用机制。正式论文写作前，需要补充真实文献、年份、作者、实验设置和引用格式。

## 内容结构

- 相关工作主题地图。
- 每类工作的对比焦点。
- 本系统可定位的贡献点。
- 需要补证的论文和实验数据。
- 局限与写作风险。

## 主题地图

| 相关工作类别 | 可能检索关键词 | 本系统对应机制 | 建议比较点 |
| --- | --- | --- | --- |
| 传统 C 到 Rust 转译 | C2Rust, transpiler, source-to-source translation, Rust migration | `translation_contract`、ContextualRustAgent、RustRepairAgent | 是否生成机械 FFI / unsafe 风格，是否有行为修复闭环 |
| 程序理解和静态分析辅助迁移 | program slicing, call graph, AST summarization, tree-sitter, code summarization | `CCodeAnalyzer`、`ModuleSplitter`、`SpecAgent` | 静态事实如何转成 LLM 可消费上下文 |
| LLM 代码生成 | LLM code generation, repository-level code generation, agentic coding | `ContextualRustAgent`、file plan、registry、`<CGR_READ>` | 按需检索和符号护栏如何控制仓库级生成 |
| LLM 程序修复 | automated program repair, compiler feedback, LLM repair, iterative repair | `RustRepairAgent`、frontier metrics、structured edits | 编译前沿验收是否优于错误数下降 |
| 检索增强代码生成 | retrieval-augmented code generation, repository context retrieval, code RAG | `RustGenerationSpecAgent`、SpecDocumentIndex、source records | 文件计划驱动检索，不是通用向量检索 |
| 测试驱动生成和修复 | test-driven repair, differential testing, oracle-guided repair | `RustTestAgent`、C binary、shell tests、snapshot | C 参考行为作为 oracle |
| 运行时调试和取证 | program debugging agents, debugger-assisted repair, instrumentation, dynamic analysis | `LogAgent`、`debug_probe`、`static_probe_update` | LLM 主动请求运行时证据 |
| 软件迁移评估 | migration benchmark, semantic equivalence, Rust safety metrics | shell pass rate、unsafe/raw pointer 指标 | 如何评价迁移正确性和 Rust 化质量 |
| 可复现 agent 系统 | LLM audit logs, experiment reproducibility, prompt logging | RoundLogger、translation metrics、repair journal | 可审计性和成本统计 |

## 可引用系统机制

相关工作章节可反复引用以下系统机制作为对比锚点：

- `translation_contract.json`：把 C 项目事实、允许 Rust 文件、依赖策略和禁止能力固化为机器可读范围契约。
- `ModuleSplitter`：用目录、调用关系、结构体共用、函数名前缀和规模阈值构造面向 prompt 预算的模块单元。
- `ContextualRustAgent`：以 `RustFilePlan` 为中心执行按需上下文生成，并通过 `<CGR_READ>` 补证据。
- `RustProjectRegistry`：维护已生成 Rust API 的轻量符号表，用于减少重复定义和跨文件猜测。
- `RustRepairAgent`：把 rustc 诊断转成诊断计划、结构化编辑、post-check 和编译前沿验收。
- `RustTestAgent`：用原始 C shell tests 和 C 参考 binary 构造行为 oracle，并通过 snapshot 回滚约束回归。
- `LogAgent` / `RuntimeProbeService`：把 stdout、stderr、trace、debug probe 和 static probe 转成可进入 prompt 的运行时证据。

## 1. 传统 C 到 Rust 转译

### 对方可能关注的问题

传统 C 到 Rust 转译通常关注：

- 是否能把 C 语法、指针、结构体和调用关系机械转换到 Rust。
- 是否保留 C ABI、`unsafe`、raw pointer 和 `libc` 依赖。
- 是否通过编译。
- 是否能作为人工重构的起点。

### 本系统的差异定位

本系统可以定位为“面向行为迁移的 agentic rewrite system”，而不是传统 source-to-source transpiler：

- 先构建 C 项目事实和迁移文档，而不是逐文件语法转写。
- 使用 `translation_contract.json` 限制生成范围，避免模型扩写功能。
- `ContextualRustAgent` 尝试生成更 Rust-native 的文件和 API，但用 contract 防止无证据功能。
- `RustRepairAgent` 和 `RustTestAgent` 把编译器和原始 C shell tests 作为外部判别器。

可引用系统机制：

- `translation_contract.generation_boundary.allowed_rust_files`。
- `forbidden_without_evidence` 禁止无证据 `ffi`、`serde`、线程安全、恢复机制等能力。
- lint 检查 raw pointer、`unsafe`、`c_void`、`repr(C)`、C 风格命名泄漏。

需要补证的数据：

- 与一个或多个传统 C 到 Rust 工具的对比结果。
- unsafe 行数、raw pointer 次数、`extern "C"` 次数、编译通过率和测试通过率。
- 人工评估 Rust API 是否更 idiomatic。

局限：

- 如果没有真实传统工具 baseline，论文中只能写设计差异，不能声称实验优于传统工具。
- 本系统仍可能生成 `unsafe` 或低质量 Rust，需以静态指标和案例说明。

## 2. 程序理解和静态分析辅助迁移

### 对方可能关注的问题

这类工作关注如何从源码中抽取：

- 函数、类型、宏、调用图、include 图。
- 模块边界和依赖关系。
- 程序切片或相关上下文。
- 面向下游任务的摘要。

### 本系统的差异定位

本系统不是追求完整 C 语义分析，而是构造 LLM 迁移所需的“足够事实”：

- `tree-sitter` 提供无完整编译数据库时的结构化事实。
- `ModuleSplitter` 用目录、调用关系、结构体共用、函数名前缀和规模阈值切分模块。
- `SpecAgent` 把静态事实转换成文档、contract 和模块级任务。

可引用系统机制：

- `func_defid`、`span`、`source`、`calls` 和 `num_lines`。
- `cohesion_score` 和 `module_units` / `cluster_units`。
- pointer / macro risk notes 作为迁移风险上下文。

需要补证的数据：

- 静态事实抽取准确率。
- 模块切分与人工模块边界的一致性。
- 模块切分信号消融对下游生成和修复的影响。

局限：

- 系统不执行完整 preprocessor，不能与 clang AST 级工具等同。
- 相关工作写法应强调“LLM context construction”，而不是声称完整静态分析贡献。

## 3. LLM 仓库级代码生成

### 对方可能关注的问题

仓库级 LLM 代码生成通常关注：

- 如何规划文件结构。
- 如何管理长上下文。
- 如何让后生成文件知道前面文件的 API。
- 如何避免重复定义和引用不存在的符号。

### 本系统的差异定位

本系统的生成阶段有两个特殊点：

- 目标是跨语言迁移，不是从自然语言需求新建软件。
- 上下文来自 C 程序事实、spec 文档和 contract，而不是普通需求文档。

可引用系统机制：

- `RustFilePlan` 将 Rust 文件绑定到 C source files 和 source functions。
- `RustProjectRegistry` 维护已生成类型、函数、字段、方法和可见性。
- `<CGR_READ>` 让模型显式请求缺失证据。
- 本地生成 `Cargo.toml` 和 `src/lib.rs`，避免格式和 re-export 漂移。

需要补证的数据：

- 与直接全文 prompt 的请求数、prompt token 和初始编译率对比。
- registry 关闭后的重复定义、private access、unresolved import 错误数量。
- `<CGR_READ>` 请求是否真实减少猜测实现。

局限：

- registry 目前是正则启发式，不是 Rust 编译器或 Rust analyzer。
- 如果相关工作使用 IDE 级索引或 AST，需明确本系统是轻量 guardrail。

## 4. 检索增强代码生成

### 对方可能关注的问题

检索增强代码生成常见问题包括：

- 检索粒度：文件、函数、section、语义块。
- 检索信号：embedding、BM25、路径、符号、调用图。
- 上下文预算：如何裁剪、排序和去重。
- 何时允许模型主动请求更多材料。

### 本系统的差异定位

本系统采用文件计划驱动的证据路由，而不是通用文档搜索：

- `RustGenerationSpecAgent` 将 Markdown 拆成 section，并融合 source records 和 translation contract。
- 检索时 source overlap 权重高，接口 / 行为 / module-spec 有额外权重。
- `plan.source_files` 存在时进行严格模块过滤。
- section 内部再拆 semantic block，按 soft budget 截取。

可引用系统机制：

- `SpecDocumentIndex` 是轻量回退索引。
- `RustGenerationSpecAgent.context_for_file()` 是 contextual 路径主要索引。
- `<CGR_READ>` 支持 spec、source、rust、registry、plan。

需要补证的数据：

- 检索 section 的人工相关性标注。
- strict source filtering 开关消融。
- soft budget 不同设置下的生成质量和成本。

局限：

- 当前不是向量检索系统，不能直接和 embedding RAG 的召回率比较，除非补实现或统一任务。
- section 抽取依赖正则和 Markdown 标题规范。

## 5. LLM 自动程序修复和编译器反馈

### 对方可能关注的问题

这类工作通常使用：

- 编译错误或测试失败作为反馈。
- LLM 生成补丁。
- 多轮修复。
- 通过 / 不通过作为停止条件。

### 本系统的差异定位

`RustRepairAgent` 的重点是把 Rust 编译修复拆成可审计状态机：

- 先诊断计划，后结构化编辑。
- 错误分批，避免长 stderr 淹没 prompt。
- 使用 C/spec/Rust 多来源证据，而不是只贴 rustc stderr。
- 使用 error frontier，而不是只看错误数量下降。

可引用系统机制：

- `ErrorOrganizerAgent` 的错误码 + 主文件聚类和源码窗口。
- `frontier_metrics` 区分 syntax blockers 与 interface blockers。
- `repair_journal.jsonl` 记录诊断、材料、编辑、post-check 和 accept reason。
- 确定性结构修复在每轮前后处理括号不平衡。

需要补证的数据：

- 当前前沿规则 vs naive error-count 规则。
- 诊断 / 编辑分离 vs 单轮自由补丁输出。
- 结构化编辑协议的失败率和破坏性编辑率。

局限：

- 默认原地修复降低了前沿验收的事务性。
- `error_signature` 当前不适合跨运行聚合。

## 6. 测试驱动修复和 differential oracle

### 对方可能关注的问题

测试驱动修复常关注：

- 单元测试或回归测试作为 oracle。
- 测试失败定位。
- 修复后不破坏已有测试。
- 避免 overfitting 到测试。

### 本系统的差异定位

本系统的 oracle 来自原 C 项目：

- C 参考 binary 代表原始行为。
- 原始 shell tests 保持只读，不由 LLM 修改。
- wrapper 把测试脚本中的项目命令映射到 Rust binary。
- 修复当前失败用例后检查 baseline pass cases。

可引用系统机制：

- `BASH_ENV` 函数映射，不污染 `PATH`。
- `ProjectSnapshot` 在单用例修复前后提供回滚边界。
- `violates_no_fake_impl()` 检测硬编码 expected output 和占位实现。
- `test_artifact_read` 支持读取 generated source、timeout context 等中间产物。

需要补证的数据：

- 是否存在 test overfitting，被 fake filter 拦截多少次。
- snapshot 回滚次数和回滚后成功率。
- RustTestAgent on/off 对最终测试通过率的影响。

局限：

- shell tests 覆盖有限，不能证明完整等价。
- 原始测试需要人工适配，相关工作比较时要说明测试基准准备方式。

## 7. LLM 调试、动态分析和 instrumentation

### 对方可能关注的问题

这类工作关注：

- LLM 是否可以主动选择调试动作。
- 调试器、日志插桩、trace 如何进入 prompt。
- 如何避免插桩污染真实代码。
- 调试动作和编辑动作如何排序。

### 本系统的差异定位

本系统把运行时取证作为 RustTestAgent 的可选增强：

- `LogAgent` 先结构化普通运行失败。
- `debug_probe` 通过 LLDB 采集 frames、locals 和 watch expressions。
- `static_probe_update` 在临时副本插入 `eprintln!` 或 `fprintf(stderr, ...)`。
- probe 与 edits 互斥，避免证据时序混乱。

可引用系统机制：

- `target = rust|c|both` 支持 Rust 和 C 侧对比。
- 最近 4 个 dynamic probe 和最近 1 个 static probe 自动进入下一轮 prompt。
- static probe 拒绝绝对路径和 `..`，只修改临时副本。

需要补证的数据：

- LogAgent / debug probe / static probe 三组消融。
- probe 成功率、失败类型和额外耗时。
- C/Rust both probe 对定位行为差异的案例。

局限：

- 动态 probe 不重放完整 shell 脚本。
- release binary 的调试信息限制 locals 质量。
- 静态 C probe 对复杂表达式和指针内容支持有限。

## 8. 软件迁移质量和 Rust 化评价

### 对方可能关注的问题

迁移质量不只包括功能通过，还包括：

- Rust 安全性和 `unsafe` 使用。
- raw pointer / FFI 泄漏。
- 模块结构和 API 设计。
- 依赖数量。
- 可维护性。

### 本系统的差异定位

本系统已有部分质量 guardrail，但还缺完整评价：

- contract 禁止无证据 FFI 和第三方依赖。
- ContextualRustAgent lint 检查 raw pointer、`unsafe`、C ABI 泄漏和 C 风格命名。
- shell tests 提供行为层验证。

可引用系统机制：

- `std_only_by_default`。
- 未授权依赖检测。
- C ABI leak lint。

需要补证的数据：

- 每个项目的 `unsafe` 行数、raw pointer 次数、`extern "C"` 次数。
- 依赖数量和未授权依赖数量。
- API Rust idiom 人工评分或静态规则。

局限：

- “地道 Rust”难以完全自动度量。
- 无 unsafe 不代表内存安全证明，尤其如果逻辑不等价。

## 9. 可复现性和 agent 可观测性

### 对方可能关注的问题

LLM agent 系统通常面临：

- prompt 和回复难复现。
- 成本统计缺失。
- 运行失败难归因。
- 模型配置和环境未固定。

### 本系统的差异定位

本系统已经有多层日志：

- shell 主日志。
- `RoundLogger` 记录 request / reply、objective、backend、duration、token usage 和 call stack。
- `translation_metrics.json` 记录总耗时和 LLM 请求数。
- `repair_journal.jsonl` 记录编译修复过程。
- `.cgr_logs` 记录测试运行证据和 probe evidence。

可引用系统机制：

- `Model.generate()` 底层统一计数和写 round log。
- round log 支持真实 token usage 或估算。
- `main.py` 在 `finally` 中保存 metrics。

需要补证的数据：

- 新增 run manifest：git commit、命令行、环境变量、模型配置、数据集 hash、输出目录。
- 阶段级成本解析脚本。

局限：

- round logs 是 Markdown，不是结构化事件表。
- 公开 artifact 前需要处理源码和 prompt 隐私。

## 写作定位建议

### 推荐主贡献表述

建议将论文贡献聚焦为 3 到 4 点：

1. **Documentation-mediated migration context：** 用 tree-sitter 事实、模块切分和 `translation_contract.json` 把 C 项目转成迁移约束。
2. **Demand-driven Rust generation with symbol guardrails：** 按文件计划检索证据，并用 registry 约束跨文件生成。
3. **Compiler-frontier guided repair：** 用 rustc 诊断分批、结构化编辑和前沿验收推进编译修复。
4. **C-reference functional repair with runtime evidence：** 用原始 shell tests、snapshot 回归保护和 LogAgent/probe 修复行为偏差。

### 不建议过度宣称

不建议写：

- “完整理解 C 语义”。应写“抽取迁移所需结构化事实”。
- “证明 Rust 与 C 语义等价”。应写“在原始 shell 测试覆盖下行为一致”。
- “自动生成安全 Rust”。应写“通过 contract 和 lint 降低 C ABI / raw pointer 泄漏，并用指标评估 unsafe 使用”。
- “通用调试器 agent”。应写“在 rtest 修复循环中提供受控 debug/static probe”。

## 需要补证的数据

| 补证项 | 目的 | 最小做法 |
| --- | --- | --- |
| 真实文献清单 | 完成相关工作章节 | 按主题检索并记录 3 到 5 篇核心工作 |
| 传统 C2Rust baseline | 支撑迁移工具对比 | 选 2 到 3 个小项目跑工具和本系统 |
| LLM 生成 baseline | 支撑上下文机制贡献 | `RustAgent`、`StableRustAgent`、`GrowthRustAgent` 对比 |
| 修复 baseline | 支撑 RustRepairAgent | legacy `CodeFixer` 或 naive repair |
| 测试修复 baseline | 支撑 RustTestAgent | 关闭 RustTestAgent / 关闭 LogAgent / 关闭 snapshot |
| 质量指标 | 支撑 Rust 化质量 | `unsafe`、raw pointer、依赖、FFI、测试污染 |
| 失败案例 | 支撑 discussion | 每类失败至少 1 个具体项目案例 |

## 局限

- 本文没有正式引用条目，不能直接作为论文相关工作最终稿。
- 若后续文献发现已有工作覆盖了相似机制，需要重新调整贡献表述，尤其是 LLM 调试、RAG 代码生成和测试驱动修复方向。
- 当前映射偏系统机制视角，缺少具体 benchmark、模型和评估协议对齐。
- 与外部工作的公平比较需要固定模型、prompt、数据集、运行预算和输出判定标准。
