# 成功指标定义

## 研究问题

本文件回答「怎样判定一次 C 到 Rust 翻译成功」的问题。成功不能只看是否生成文件，还需要分层区分：是否完成流水线、是否能被 Rust 工具链接受、是否通过 C shell 测试、是否保持回归安全，以及是否减少 `unsafe` 和裸指针等迁移风险。

核心研究问题包括：

- 编译成功、release build 成功和功能测试成功应如何区分？
- `RustRepairAgent` 的过程指标如何进入最终成功率解释？
- `RustTestAgent` 的单用例通过率、项目级全通过率和回归保护如何量化？
- 质量指标如何避免被「编译能过但 Rust 不安全」的结果掩盖？

## 指标定义或数据流

建议把结果表分为 4 层，不把它们混成一个二值指标。

| 层级 | 指标 | 定义 | 主要数据源 |
| --- | --- | --- | --- |
| L0 流水线完成 | `pipeline_completed` | 主入口进程退出码为 0，且输出目录存在 Rust crate 或明确记录失败阶段 | `log/agent-*.log`、`output*/<project>/` |
| L1 编译接受 | `cargo_check_passed` | 最终 Rust crate 执行 `cargo check` 通过 | `repair_journal.jsonl`、最终复跑 |
| L1 编译接受 | `release_build_passed` | 最终 Rust crate 执行 `cargo build --release` 通过 | `RustRepairAgent` 结果、rtest 前置 build |
| L2 行为正确 | `functional_project_passed` | `TestRunSummary.all_passed == true`，即 `failed == 0` 且 `total > 0` | `rtest` 日志、`TestRunSummary` |
| L2 行为正确 | `functional_case_pass_rate` | `sum(passed_cases) / sum(total_cases)`，只在有测试的项目上统计 | `TestRunner.run_all()` |
| L3 修复稳健性 | `regression_safe_accepts` | 当前失败用例修复后，baseline pass cases 没有回归 | rtest 回归检查日志 |
| L3 过程改进 | `repair_lift` | 修复后通过数减去首跑通过数，或错误前沿推进次数 | `repair_journal.jsonl`、rtest summary |
| L4 代码质量 | `unsafe_rate` | `unsafe` block 覆盖行数 / Rust 总行数 | `unsafe_metrics.json` 或复算 |
| L4 代码质量 | `raw_ptr_type_occurrences` / `raw_ptr_dereferences` | `*const` / `*mut` 类型语法次数，以及可保守推断的裸指针解引用次数 | `raw_ptr_stats.json` |

核心公式如下：

```text
compile_pass_rate = count(projects with final cargo_check_passed) / count(compile_eligible_projects)

release_build_pass_rate = count(projects with release_build_passed) / count(compile_eligible_projects)

functional_project_pass_rate = count(test_eligible projects with all_passed) / count(test_eligible_projects)

functional_case_pass_rate = sum(passed cases across projects) / sum(total cases across projects)

repair_lift_cases = final_passed_cases - initial_passed_cases

llm_efficiency = successful_projects / total_llm_request_count
```

其中 `compile_eligible_projects` 至少应满足 Rust crate 已生成；`test_eligible_projects` 还要满足 C oracle 可构建且有可执行 shell 测试。

## 关键工程细节

- **`cargo check` 与 release build 需要分开。** `RustRepairAgent` 会先用 `cargo check` 推进错误前沿，`cargo check` 通过后再运行 `cargo build --release`。release build 通过仍不代表功能正确。
- **`RepairRunResult.test_passed` 命名容易误导。** 在当前编译修复阶段，它实际表示 release build 是否通过，不是 shell 测试或 `cargo test` 通过。
- **功能测试不是 `cargo test`。** `RustTestAgent` 使用 C 项目的 shell 测试作为 oracle，通过 `TestRunner` 把原测试脚本映射到 Rust binary。
- **项目级功能成功要求 `total > 0`。** `TestRunSummary.all_passed` 要求没有失败且至少存在一个测试。无测试项目不能算功能测试全通过。
- **失败签名口径不一致。** rtest 的 `failure_signature()` 使用 SHA-256，适合跨运行聚合；编译修复的 `error_signature()` 使用 Python `hash()`，只适合同一进程内比较。
- **质量指标应在最终 crate 上统一复算。** 历史 `raw_ptr_stats.json` 和 `unsafe_metrics.json` 存放位置不统一，且 `get_unsafe_rate.py` 当前入口不是面向 `output*` 的批处理入口。
- **过程指标解释最终指标。** `accepted_as_best`、`accept_reason`、`error_signature_stall`、`round_timeout`、材料请求次数和编辑次数可以解释为什么某些项目失败。

## 可引用代码证据

| 论点 | 代码证据 |
| --- | --- |
| `TranslationMetrics` 记录开始、结束、耗时和 LLM 请求数 | `src/utils/translation_metrics.py:23-64` |
| 主流程保存 `translation_metrics.json` 并打印总耗时和请求轮数 | `src/agent/main.py:785-793` |
| 每次模型调用会递增 LLM 请求计数 | `src/llm/model.py:28` |
| `RustRepairAgent` 包装 `cargo check`、`cargo test` 和 `cargo build --release` | `src/agent/rust_repair_agent.py:210-222` |
| release build 结果构造 `RepairRunResult` | `src/agent/rust_repair_agent.py:254-260` |
| 编译错误数量、错误签名和 frontier metrics | `src/agent/rust_repair_agent.py:556`、`src/agent/rust_repair_agent.py:566`、`src/agent/rust_repair_agent.py:604` |
| `iteration_result` 记录是否推进 best、验收理由和前沿指标 | `src/agent/rust_repair_agent.py:4469-4491` |
| `TestCaseResult` 记录 exit code、stdout、stderr、duration 和 trace | `src/agent/rtest/models.py:14-22` |
| rtest 失败签名使用 SHA-256 稳定摘要 | `src/agent/rtest/models.py:31-36` |
| `TestRunSummary.all_passed` 要求 `failed == 0` 且 `total > 0` | `src/agent/rtest/models.py:42-50` |
| `TestRunner.run_all()` 聚合单用例结果 | `src/agent/rtest/test_runner.py:236-254` |
| 当前用例通过后执行 baseline 回归检查 | `src/agent/rtest/rust_test_agent.py:1997`、`src/agent/rtest/rust_test_agent.py:2102` |
| 裸指针计数器输出类型出现、解引用和源码位置 | `scripts/count_raw_ptrs.py:833-846`、`scripts/count_raw_ptrs.py:973-986` |
| `unsafe_rate` 由 unsafe 行数除以 Rust 总行数 | `scripts/get_unsafe_rate.py:273-294` |

## 实验钩子

- **主结果表。** 每个项目报告 `cargo_check_passed`、`release_build_passed`、`functional_project_passed`、`functional_case_pass_rate`、`llm_request_count`、`elapsed_seconds`、`unsafe_rate` 和裸指针计数。
- **阶段漏斗图。** 从 `generated_crate` 到 `cargo_check_passed`、`release_build_passed`、`functional_project_passed` 逐层统计掉队项目。
- **修复收益表。** 对有 repair journal 的项目统计初始错误数、最终错误数、accepted frontier 次数、round timeout 次数和最终编译状态。
- **功能修复收益。** 对有 rtest 日志的项目统计首跑 passed/failed、最终 passed/failed、每个失败用例平均修复轮数、回归次数。
- **质量约束。** 在编译通过项目上单独报告 `unsafe_rate` 和裸指针计数，避免失败项目因为没有 Rust 代码而被错误纳入质量均值。
- **效率指标。** 报告每个成功项目的 LLM 请求数、每个新增通过测试用例的 LLM 请求数，以及每 100 次 LLM 请求带来的成功项目数。

## 局限与反例

- `cargo check` 通过不代表 release build 通过，release build 通过也不代表行为等价。
- `functional_case_pass_rate` 可能被测试数很多的单个项目主导，论文应同时报告 macro 平均和 micro 平均。
- shell 测试可能覆盖不均。有些项目无测试，只能纳入编译和质量指标。
- rtest 日志目前主要是文本 summary，若要长期稳定聚合，最好增加结构化 `rtest_summary.json`。
- 编译 `error_signature` 使用 Python `hash()`，跨进程不稳定，不能直接作为跨项目聚类键。
- `unsafe_rate` 的历史 JSON 样例中 `0.00` 可能来自格式化显示，真实计算应保留足够小数位。
- LLM 自述的 `updated_summary` 不能当作成功判据，只能作为解释材料。

## 可写入论文位置

- **实验设置：指标定义。** 明确 L0 到 L4 的成功层级，避免把编译、测试和质量混为一个指标。
- **结果章节：主表和漏斗图。** 用阶段漏斗展示系统从生成到编译、release build、功能测试的通过率。
- **分析章节：修复过程指标。** 用 frontier、修复轮数和回归次数解释成功与失败。
- **威胁与局限：oracle 与覆盖率。** 说明 shell 测试覆盖、无测试项目和编译指标的边界。
