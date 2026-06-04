# A0-02：`main.py` 主工作流调研

日期：2026-06-04  
责任范围：`src/agent/main.py`，关联 `src/config/config.py`

## 研究问题

本文件回答论文中「系统内部如何把多 agent 翻译组织成闭环」的问题。`scripts/agent.sh` 负责入口参数和环境，`src/agent/main.py` 则是端到端状态机：它决定哪些阶段运行、哪些阶段跳过、哪些产物进入下游，以及何时允许进入编译修复和功能测试。

核心研究问题包括：

- 主流程如何把 C 工程预检、C 语义文档、Rust 生成、编译修复和功能测试串成统一闭环？
- `SpecAgent`、`ContextualRustAgent`、`RustRepairAgent`、`RustTestAgent` 如何作为默认主线互相传递上下文？
- 哪些参数可以作为论文中的消融开关？
- 主流程有哪些工程保护，哪些地方仍是复现或统计上的薄弱点？

## 流程 / 数据流

### 总体阶段

```text
main.py
  -> argparse 解析命令行
  -> translation_metrics.start()
  -> 创建 output_dir 与可写 c_docs
  -> Config(local_config.json)
  -> 打印运行配置
  -> 校验互斥 agent 选择
  -> C 项目 clean_and_build 预检
  -> 步骤 1：SpecAgent 或 CDocAgent 生成 C 文档
  -> 步骤 1.5：可选 SpecJsonAgent 压缩 spec 文档
  -> 步骤 1.6 / 1.7：可选 PointerAgent / MacroAgent
  -> 收集 doc_paths 与 source_json_path
  -> 步骤 2：RustAgent 系列生成 Rust 项目
  -> 步骤 2.5：UnfinishedCodeAgent 补全 todo!/unimplemented!
  -> 步骤 3：RustRepairAgent 或 legacy CodeFixer
  -> 步骤 4：RustTestAgent 或 legacy TestFixer
  -> finally：translation_metrics.finish() 并写 translation_metrics.json
```

主流程的输入和输出可以抽象为：

| 阶段 | 输入 | 输出 | 外部判别器 |
| --- | --- | --- | --- |
| C 预检 | `datasets/<project>` | 可执行 C 项目、Makefile、测试目录 | `make clean/build` |
| C 理解 | C 项目源码 | `output/<project>/c_docs` | tree-sitter、LLM 文档生成 |
| 上下文汇总 | `c_docs`、`src/parse/res/<project>.json` | `doc_paths`、`source_json_path` | 文件存在性检查 |
| Rust 生成 | 文档、C 项目路径、源码 JSON | Rust crate | agent 内部生成约束 |
| 未完成补全 | Rust crate | 修补后的 Rust 文件 | `todo!`、`unimplemented!` 扫描 |
| 编译修复 | Rust crate、C docs、C 项目路径 | 编译更接近通过的 Rust crate | `cargo check`、`cargo build --release` |
| 功能测试 | Rust release binary、C 参考项目、shell 测试 | 行为修复后的 Rust crate | C 参考行为、shell 测试 |
| 指标落盘 | 全流程状态 | `translation_metrics.json` | monotonic clock、LLM 调用计数 |

### 文档路径的数据流

`main.py` 不把整个输出目录无差别传给 Rust 生成器，而是按分析路径构造 `doc_paths`：

- `SpecAgent` + `SpecJsonAgent`：优先传入 `c_docs/spec_json/spec_context.json`，并追加启用的 pointer/macro 辅助证据。
- `SpecAgent` 无 JSON 中间层：传入 `.specify/memory`、`docs/rewrite-context`、`specs` 等目录。
- `CDocAgent`：传入 `final_project_overview.md`。
- 非 Spec 路径下的 PointerAgent / MacroAgent：追加 `pointer_guidance.md` 和 `macro_guidance.md`。
- 若存在 `src/parse/res/<project>.json`，则作为 `source_json_path` 传给 Rust 生成器。

这一层是论文中「上下文边界」的重要证据：主流程显式选择文档和源码索引，而不是把所有输出产物混合注入。

### 修复与测试的数据流

编译修复阶段有两条路径：

- 新路径：`RustRepairAgent`。开启 `--use-rust-repair-agent` 后替代默认 `CodeFixer/TestFixer`，并接收 `ErrorOrganizerAgent`、C 项目路径和 `c_docs` 路径。
- 旧路径：`CodeFixer` + `TestFixer`。仅在未开启 RustRepairAgent 和 RustTestAgent 时作为 legacy baseline。

功能测试阶段由 release build gate 控制。只有当前 Rust 项目通过 `cargo build --release`，`RustTestAgent` 才会执行 C 参考行为驱动测试；否则主流程明确跳过功能测试，避免在没有 release 二进制时产生无意义测试失败。

## 关键工程细节

- **阶段开关集中化。** 文件顶部定义 `should_run_primary_c_analysis`、`should_run_spec_json_stage`、`should_run_rust_repair_stage`、`should_run_rust_test_agent_stage` 等函数，使主流程分支更容易对应论文中的实验开关。
- **冻结文档模式。** `--freeze-c-docs` 通过 `c_docs_writable(args)` 禁止所有写入 `c_docs` 的阶段，只复用已有文档。这对「固定 C 理解结果，只比较 Rust 生成或修复策略」的实验很关键。
- **C 项目启动预检。** 主流程在任何 LLM 翻译前调用 `CProjectBuilder.clean_and_build`，验证 C 项目本身可构建，并打印 Makefile、test 目录和二进制路径。这样可以排除「输入 C 项目本身坏掉」导致的翻译失败。
- **Rust 生成器互斥。** `--use-stable-rust-agent`、`--use-growth-rust-agent`、`--use-contextual-rust-agent` 只能开启一个，避免实验配置同时混入多个生成策略。
- **ContextualRustAgent 特化配置。** 当启用 ContextualRustAgent 时，主流程设置 `entry_kind`，并通过 `configure_optional_evidence` 传入 pointer/macro 开关，使入口策略和辅助证据成为显式实验变量。
- **续跑模式是能力探测式。** 主流程只在 agent 暴露 `continue_mode` 属性时设置它，因此 `--continue` 不要求所有 Rust 生成器实现同一续跑接口。
- **未完成实现补全在修复前执行。** `UnfinishedCodeAgent` 在编译修复之前扫描并续写 `todo!`、`unimplemented!` 等占位，避免编译修复 agent 把明显未完成的文件误判为普通编译错误。
- **ErrorOrganizerAgent 可注入多个阶段。** 开启后，错误整理器会传给 `RustRepairAgent` 或 legacy fixer，用统一批大小控制错误诊断上下文。
- **release build gate 保护 RustTestAgent。** `run_optional_rust_test_agent` 进入前再次检查 `cargo build --release`，避免功能测试阶段消耗 prompt 预算处理不存在的二进制。
- **指标在 `finally` 中写出。** 即使中途 `return 1`，主流程也会执行 `translation_metrics.finish()` 并保存 `translation_metrics.json`，保留失败运行的基本成本数据。

## 可引用代码证据

| 论点 | 代码证据 |
| --- | --- |
| 阶段开关函数集中定义 | `src/agent/main.py:43-103` |
| cargo gate 使用 `cargo check` 与 `cargo build --release` | `src/agent/main.py:106-130` |
| RustTestAgent 运行前要求 release build 通过 | `src/agent/main.py:133-177` |
| C 项目启动预检使用 `CProjectBuilder.clean_and_build` | `src/agent/main.py:180-203` |
| Rust 生成器模式选择 | `src/agent/main.py:206-213` |
| RustRepairAgent 配置 C 项目和 `c_docs` 上下文 | `src/agent/main.py:216-259` |
| CLI 暴露 freeze、SpecAgent、SpecJsonAgent、PointerAgent、MacroAgent | `src/agent/main.py:267-308` |
| CLI 暴露 Rust 生成器、entry kind、ErrorOrganizerAgent | `src/agent/main.py:309-339` |
| CLI 暴露修复、RustTestAgent、LogAgent、continue 参数 | `src/agent/main.py:341-428` |
| 运行开始时创建输出、加载配置、设置 round log 项目名 | `src/agent/main.py:432-448` |
| 互斥 Rust 生成器校验 | `src/agent/main.py:481-492` |
| C 文档生成阶段选择 SpecAgent 或 CDocAgent | `src/agent/main.py:503-528` |
| Spec JSON、PointerAgent、MacroAgent 可选阶段 | `src/agent/main.py:530-572` |
| `doc_paths` 和 `source_json_path` 收集 | `src/agent/main.py:573-620` |
| ContextualRustAgent 配置并调用 `generate_from_docs` | `src/agent/main.py:622-650` |
| UnfinishedCodeAgent 补全阶段 | `src/agent/main.py:659-688` |
| RustRepairAgent 与 legacy fixer 分支 | `src/agent/main.py:690-758` |
| RustTestAgent 最终 gate | `src/agent/main.py:760-767` |
| 指标在 `finally` 中保存 | `src/agent/main.py:781-796` |
| `Config` 从 `local_config.json` 加载模型、API 和 round log 配置 | `src/config/config.py:1-98` |

## 实验钩子

| 实验目标 | 参数或产物 | 说明 |
| --- | --- | --- |
| 比较 C 理解路径 | `--use-spec-agent` vs 默认 `CDocAgent` | 默认入口会开启 SpecAgent；消融需用 `CGR_NO_DEFAULT_FLAGS=1` 显式配置 |
| 固定 C 文档，只比较生成和修复 | `--freeze-c-docs` | 跳过所有写 `c_docs` 的阶段 |
| 续跑生成过程 | `--continue` | 仅对支持 `continue_mode` 的生成器生效 |
| 比较 Rust 生成策略 | `--use-contextual-rust-agent`、`--use-growth-rust-agent`、`--use-stable-rust-agent` | 三者互斥，适合生成策略消融 |
| 比较 crate 入口策略 | `--rust-entry-kind auto/main/lib` | 仅 ContextualRustAgent 使用 |
| 比较错误整理效果 | `--use-error-organizer-agent --error-batch-size N` | 可测错误批大小对修复轮数和成功率的影响 |
| 比较编译修复路径 | `--use-rust-repair-agent` vs legacy `CodeFixer` | 新路径替代旧路径 |
| 比较行为测试修复 | `--use-rust-test-agent`、`--rust-test-agent-max-iterations`、`--rust-test-agent-timeout-seconds` | 观测 shell 用例通过率和修复轮数 |
| 比较运行时证据增强 | `--use-log-agent --log-agent-max-debug-probes N` | 观测失败用例修复质量和额外请求成本 |
| 成本统计 | `translation_metrics.json`、round logs | 当前总指标较粗，round logs 可补充每轮 prompt/reply 与 token 信息 |

建议实验记录的最小字段：

| 字段 | 来源 |
| --- | --- |
| 项目名 | `args.c_project_path` 或入口命令 |
| 模型名与远程模型名 | `Config.model_name`、`Config.api_model` |
| 分析路径 | `args.use_spec_agent` |
| Rust 生成器 | `selected_rust_agent_mode(args)` |
| 修复器 | `args.use_rust_repair_agent` |
| 测试器 | `args.use_rust_test_agent` |
| 是否开启 LogAgent | `args.use_log_agent` |
| LLM 请求轮数与耗时 | `translation_metrics.json` |
| 编译状态 | `cargo check`、`cargo build --release` gate |
| 功能测试状态 | RustTestAgent summary 或 rtest 日志 |

## 局限与反例

- **主流程仍偏单体状态机。** `main.py` 串联所有阶段，适合读端到端流程，但阶段级时间、阶段级 token 和阶段级失败原因没有统一结构化事件流。
- **`doc_paths` 只检查存在性。** 当前主流程只要文档路径存在就进入下游，没有验证 spec 文档是否完整、是否过期、是否与当前 C 项目版本一致。
- **`--freeze-c-docs` 依赖人工保证文档可用。** 如果冻结模式下 `c_docs` 缺失或来自旧版本，主流程可能只在找不到文档时失败，不能识别「文档存在但 stale」。
- **C 预检只覆盖 build，不等于行为 oracle 完整可用。** `clean_and_build` 能证明 C 项目可构建，但测试脚本质量、测试覆盖率和 C 参考输出稳定性仍由 RustTestAgent 阶段承担。
- **`compile_ready` 的语义需要论文中小心表述。** RustRepairAgent 分支要求 `repair_result.check_passed and repair_result.test_passed` 才设为 ready，但后面还会用 `cargo_build_release_passes` 兜底；不同字段名和实际 cargo 命令之间需要在 A4 调研中进一步核实。
- **多模型实验缺少输出隔离策略。** `main.py` 默认输出路径由入口给定，若同一项目多次运行不同模型，需要外部指定不同 `output_dir` 或清理历史产物。
- **异常处理主要依赖 `finally`。** 指标会落盘，但阶段失败原因大多留在文本日志中，没有统一错误码或 JSON summary。
- **可选 PointerAgent / MacroAgent 在 Spec 路径中语义不同。** 非 Spec 路径下它们是步骤 1.6 / 1.7；Spec 路径下开关传给 `SpecAgent`，后续再收集 `specs/**/pointer.md`、`macro.md`。论文中要避免把两种路径混为同一实现。

## 可写入论文位置

- **方法章节：多阶段迁移框架。** `main.py` 可以作为系统流程图的主证据，展示「C 预检 -> 语义文档 -> Rust 生成 -> 未完成补全 -> 编译修复 -> 行为测试修复」。
- **系统实现：阶段 gate 与数据流。** 说明每个阶段的输入输出、失败条件和进入下一阶段的 gate。
- **实验设置：消融变量。** CLI 参数可以直接映射到实验表中的 independent variables。
- **讨论章节：工程局限。** 单体状态机、文档 stale 风险、粗粒度指标和多模型输出隔离问题，适合写入 threats to validity。
- **附录：复现实验配置。** 把 `main.py` 参数表和默认入口参数组合整理成可复跑命令。
