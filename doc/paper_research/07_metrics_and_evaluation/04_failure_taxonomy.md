# 失败分类体系

## 研究问题

本文件回答「端到端失败应如何归因」的问题。C 到 Rust 翻译系统的失败可能发生在 C oracle 构建、上下文生成、Rust 生成、编译修复、功能测试、运行时取证或质量约束等不同层级。若只报告最终未通过，会丢失方法分析价值。

核心研究问题包括：

- 如何把编译错误、测试失败、超时、协议失败和回归统一归类？
- 哪些失败可以由现有日志自动识别，哪些需要人工复核？
- 如何区分系统机制失败和数据集 oracle 失败？
- 失败分类如何服务于论文中的 error analysis 和后续消融？

## 指标定义或数据流

失败分析建议采用「首个阻塞失败」和「最终残留失败」两套标签：

```text
run
    -> first_blocking_failure:
       第一个导致阶段无法继续的失败
    -> terminal_failure:
       达到预算或流程结束时仍存在的失败
    -> transition_events:
       repair/test loop 中失败类型如何变化
```

分类来源如下：

| 数据源 | 可提取信号 |
| --- | --- |
| `log/agent-*.log` | 入口失败、Python 环境失败、阶段启停、最终退出码 |
| `translation_metrics.json` | 是否完成、总耗时、LLM 请求数 |
| `log/round_logs/**/*.md` | prompt 目标、token、LLM 协议输出、调用栈 |
| `repair_journal.jsonl` | 编译错误数量、签名、frontier、编辑、停滞、timeout、验收理由 |
| `log/rtest-*.log` | 测试总数、单用例结果、超时、回滚、协议错误 |
| `TestCaseResult` | exit code、stdout/stderr tail、duration、trace 和稳定 failure signature |
| `.cgr_logs/*.json` | runtime evidence、debug probe、static probe 结果 |
| `raw_ptr_stats.json` / `unsafe_metrics.json` | 编译或测试成功后的质量风险 |

## 分类表

| 类别 | 标签 | 判定信号 | 典型处理 |
| --- | --- | --- | --- |
| 数据集 / oracle | `D0_C_BUILD_FAILURE` | C 项目 clean/build 失败 | 从功能测试 eligible 集合剔除，单独报告 |
| 数据集 / oracle | `D1_TEST_PORTABILITY` | shell 测试依赖绝对路径、非 bash、环境差异或 fixture 缺失 | 人工预处理或标记不可测 |
| 上下文 | `S0_SPEC_MISSING_OR_STALE` | repair/test 反复请求缺失 spec 或 C 文件，或材料路径无效 | 检查 `c_docs` 和 read request |
| 上下文 | `S1_CONTEXT_BUDGET_EXHAUSTED` | round log token 过大、重复材料请求、无新增材料 | 调整 prompt budget 或材料索引 |
| Rust 生成 | `G0_NO_CRATE_OR_BAD_LAYOUT` | 缺少 `Cargo.toml`、src 结构错误、模块文件缺失 | 生成阶段错误，或 repair 创建缺失文件 |
| 编译 | `C0_SYNTAX_BLOCKER` | rustc 语法、括号、delimiter、parse 错误 | 优先结构修复 |
| 编译 | `C1_MODULE_INTERFACE` | `E0583`、`E0432`、missing module、unresolved import | 模块规划和 symbol registry 分析 |
| 编译 | `C2_TYPE_TRAIT_BORROW` | `E0277`、`E0308`、`E0502` 等类型、trait、借用错误 | repair frontier 和 C/spec 材料 |
| 编译 | `C3_DEPENDENCY_BUILD_SCRIPT` | 依赖、`build.rs`、feature 或链接错误 | Cargo 配置分析 |
| 编译 | `C4_RELEASE_ONLY_FAILURE` | `cargo check` 过但 `cargo build --release` 失败 | 单独报告 release build failure |
| Repair 协议 | `R0_LLM_JSON_PROTOCOL` | JSON 无法解析、字段超长、缺少 required 字段 | 统计为协议成本和失败因素 |
| Repair 过程 | `R1_NO_NEW_MATERIAL_LOOP` | 重复 read request，没有新增材料 | 归因为上下文检索或策略停滞 |
| Repair 过程 | `R2_EDIT_REJECTED_OR_DESTRUCTIVE` | 路径越界、stub、破坏性缩短、测试污染被拒 | 归因为安全防护触发 |
| Repair 过程 | `R3_FRONTIER_STALL_OR_TIMEOUT` | `error_signature_stall`、`round_timeout`、预算耗尽 | 归因为 repair 未推进 |
| 功能测试 | `T0_WRAPPER_OR_PATH` | wrapper、`PATH`、alias、bash 不可用 | 测试适配层问题 |
| 功能测试 | `T1_OUTPUT_MISMATCH` | exit code 或 stdout/stderr 与 shell 期望不符 | 行为语义错误 |
| 功能测试 | `T2_TIMEOUT_OR_HANG` | exit code 为 -1，stderr 包含 timeout，duration 接近 timeout | 死循环、阻塞 I/O、环境等待 |
| 功能测试 | `T3_FILE_ENV_FIXTURE` | 临时文件、权限、locale、fixture 差异 | 测试环境或文件语义错误 |
| 功能测试 | `T4_REGRESSION` | 当前用例通过后 baseline pass cases 失败 | 回滚并记录回归 |
| 取证 | `P0_PROBE_UNAVAILABLE` | LLDB 不可用、断点无效、probe 上限耗尽 | probe 消融或环境报告 |
| 质量 | `Q0_UNSAFE_OR_RAW_PTR_RISK` | `unsafe_rate` 或裸指针计数超阈值 | 作为质量失败或二级风险 |

## 关键工程细节

- **编译失败可由 journal 自动抽取。** `pre_llm_no_fallback`、`llm_repair`、`post_check` 和 `iteration_result` 中包含错误数、签名、材料、编辑和验收结果。
- **功能失败已有稳定签名。** `TestCaseResult.failure_signature()` 对 exit code、stderr、stdout 和 trace 做归一化后取 SHA-256 前缀，适合跨运行聚类。
- **超时是一级失败类型。** `TestRunner` 在超时时写 `timeout_stdout.txt`、`timeout_stderr.txt`、`timeout_trace.txt` 和 `timeout_context.txt`，可定位 hang 子命令。
- **回归不是普通失败。** 当前用例通过但 baseline pass cases 失败，说明局部修复破坏已验证行为，应作为 `T4_REGRESSION` 单独统计。
- **probe 失败不应算作功能失败。** LLDB 不可用或断点无效只说明诊断能力受限，最终功能状态仍由 shell 测试决定。
- **质量风险是成功后的二级标签。** `Q0_UNSAFE_OR_RAW_PTR_RISK` 主要用于区分「通过但不够 idiomatic/safe」的 Rust，不应直接混入编译失败。

## 可引用代码证据

| 论点 | 代码证据 |
| --- | --- |
| 编译错误数量和签名抽取 | `src/agent/rust_repair_agent.py:556`、`src/agent/rust_repair_agent.py:566` |
| `frontier_metrics` 区分语法和接口阻塞 | `src/agent/rust_repair_agent.py:604-654` |
| frontier 验收策略 | `src/agent/rust_repair_agent.py:662-732` |
| repair 轮内 timeout 记录 | `src/agent/rust_repair_agent.py:3903-3919` |
| error signature stall 记录 | `src/agent/rust_repair_agent.py:4221-4263` |
| `iteration_result` 记录终态、验收和指标 | `src/agent/rust_repair_agent.py:4469-4491` |
| `TestCaseResult` 包含 stdout、stderr、duration、trace 和 run dir | `src/agent/rtest/models.py:14-22` |
| rtest 失败签名归一化并使用 SHA-256 | `src/agent/rtest/models.py:31-61` |
| `TestRunner.run_single()` 捕获 stdout/stderr/exit code/duration | `src/agent/rtest/test_runner.py:160-231` |
| 超时 artifact 写入 | `src/agent/rtest/test_runner.py:322-364` |
| 首次 `run_all()` 不抓 trace，失败修复时按需抓 trace | `src/agent/rtest/test_runner.py:236-255` |
| rtest 过滤 fake implementation 和 expected output 硬编码 | `src/agent/rtest/rust_test_agent.py:673`、`src/agent/rtest/rust_test_agent.py:1281` |
| 当前用例通过后进行回归检查 | `src/agent/rtest/rust_test_agent.py:1997-2030` |
| 裸指针计数输出源码位置 | `scripts/count_raw_ptrs.py:833-846` |

## 实验钩子

- **失败漏斗。** 对每个项目记录 first blocking failure，统计各类别占比。
- **终态失败表。** 对最终未通过项目记录 terminal failure，区分「未生成」「未编译」「未通过测试」「质量风险」。
- **错误转移图。** 从 `repair_journal.jsonl` 统计错误类别如何从 `C0_SYNTAX_BLOCKER` 转为 `C1_MODULE_INTERFACE` 或 `C2_TYPE_TRAIT_BORROW`，验证 frontier 机制是否暴露更深错误。
- **rtest top failures。** 按 `failure_signature()` 聚合最常见行为失败，人工标注是输出 mismatch、timeout 还是 fixture/env 问题。
- **协议成本。** 统计 `R0_LLM_JSON_PROTOCOL` 和 `R1_NO_NEW_MATERIAL_LOOP` 消耗了多少 LLM 轮次。
- **回归分析。** 统计 `T4_REGRESSION` 出现次数、回滚成功率和引发回归的编辑类型。
- **质量风险阈值。** 在通过项目中设置 `unsafe_rate > 0` 或裸指针计数 > 0 的二级标签，分析安全性和成功率的权衡。

## 局限与反例

- 编译分类依赖 rustc 英文诊断和错误码，未来 rustc 文案变化可能影响自动规则。
- `interface_blockers` 当前是模式类型计数，不是具体错误实例数。
- 编译 `error_signature` 使用 Python `hash()`，不能跨进程作为稳定类别 ID。
- rtest 日志中的协议错误多为文本输出，若要全自动聚合，需要新增结构化事件日志。
- 有些失败是复合原因，例如模块缺失导致类型错误雪崩，自动分类应优先记录最早阻塞信号。
- shell 测试失败不一定代表 Rust 语义错误，也可能是测试预处理、wrapper 或平台差异。
- `Q0_UNSAFE_OR_RAW_PTR_RISK` 的阈值需要按项目类型设定。系统级 C 项目可能天然需要少量 `unsafe`。

## 可写入论文位置

- **结果分析：失败分类。** 展示首个阻塞失败和最终失败的分布。
- **方法分析：frontier 进展。** 用错误类别转移说明修复从语法层推进到接口和行为层。
- **威胁与局限：oracle 与平台。** 说明 C build、shell 测试和 probe 环境导致的非翻译失败。
- **附录：分类规则。** 放自动规则、人工复核准则和代表性失败样例。
