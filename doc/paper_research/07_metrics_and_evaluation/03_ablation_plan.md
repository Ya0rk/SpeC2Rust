# 消融实验计划

## 研究问题

本文件回答「如何证明默认主线中每个组件确实有贡献」的问题。默认主线来自 `scripts/agent.sh`：`SpecAgent -> ContextualRustAgent -> RustRepairAgent -> RustTestAgent`，并默认开启 `ErrorOrganizerAgent`。消融实验应在固定数据集、模型、预算和输出目录策略下，一次只改变一个机制。

核心研究问题包括：

- `SpecAgent` 和 `ContextualRustAgent` 是否比旧式全量上下文或直接生成更稳？
- `RustRepairAgent` 和 error batching 是否显著提高编译通过率？
- `RustTestAgent`、LogAgent 和 probe 是否显著提高功能通过率？
- 修复预算、prompt budget 和回归保护对成功率与成本有什么影响？

## 指标定义或数据流

建议使用统一实验数据流：

```text
dataset split
    -> fixed model/backend/local_config
    -> fixed run root: output_runs/<date>/<model>/<config>/<project>
    -> scripts/agent.sh or scripts/rtest_agent.sh
    -> collect:
       - final cargo check/build result
       - rtest summary
       - translation_metrics.json
       - repair_journal.jsonl
       - round_logs
       - raw_ptr_stats.json / unsafe_metrics.json
```

消融表应至少包含以下指标：

| 指标 | 含义 |
| --- | --- |
| `compile_pass_rate` | 最终 `cargo check` 通过项目比例 |
| `release_build_pass_rate` | 最终 `cargo build --release` 通过项目比例 |
| `functional_project_pass_rate` | 有测试项目中全套 shell 测试通过的比例 |
| `functional_case_pass_rate` | shell 用例级通过率 |
| `mean_llm_requests` | 每项目平均 LLM 请求数 |
| `mean_elapsed_seconds` | 每项目平均端到端耗时 |
| `mean_repair_iterations` | 每项目平均编译修复轮数 |
| `mean_test_repair_attempts` | 每失败用例平均测试修复轮数 |
| `regression_count` | 当前用例修复后破坏 baseline pass cases 的次数 |
| `unsafe_rate` / `raw_ptr_count` | 通过项目的安全性质量指标 |

## 关键工程细节

- **默认 flags 是实验基线。** `scripts/agent.sh` 默认开启 Spec、ContextualRust、RustRepair、RustTest 和 ErrorOrganizer，且 repair/test 默认预算为 64 轮。
- **关闭默认 flags 要重建参数集。** `CGR_NO_DEFAULT_FLAGS=1` 会禁用默认参数。由于布尔 `store_true` 选项不能通过后置参数关闭，涉及关闭默认 agent 的消融需要使用该环境变量。
- **用户追加参数在最后。** 对值型参数，后置 CLI 参数通常可以覆盖默认值，例如迭代预算、prompt budget 和 binary name。
- **rtest 旁路适合固定上游。** `scripts/rtest_agent.sh` 可以固定 C/Rust 路径，只消融功能测试修复、LogAgent、测试超时和 prompt budget。
- **有些消融需要代码级钩子。** 例如禁用 fake implementation 过滤、禁用回归检查、替换 frontier 验收策略，目前不是所有都有 CLI 开关。论文中要区分「可直接运行」和「需要小补丁」。
- **输出目录必须隔离。** 默认 `output/<project>` 不带模型和配置维度，消融运行应改用单独 run root 或在每轮前归档。

## 消融矩阵

| 消融项 | 对照组 | 处理组 | 运行方式 | 主要观察 |
| --- | --- | --- | --- | --- |
| 默认主线 | `agent.sh` 默认 flags | 无 | 直接运行 | 主结果 |
| 无默认主线 | 默认主线 | `CGR_NO_DEFAULT_FLAGS=1` 后显式选择最小 agent 链 | 直接运行，需补齐参数 | 验证闭环相对最小链路的收益 |
| Rust 生成策略 | `ContextualRustAgent` | legacy `RustAgent` 或其他 baseline | 关闭默认 flags 后切换生成器 | 编译通过率、prompt token、生成文件漂移 |
| Spec 文档 | `SpecAgent` | 关闭 Spec 或使用 legacy C 文档 | 关闭默认 flags 后重建链路 | 上下文质量、read request、编译成功率 |
| 编译修复 | 开启 `RustRepairAgent` | 关闭 repair，直接进入测试或结束 | 关闭默认 flags 或设置 repair 预算为 0 | 编译通过率和 release build 通过率 |
| 错误分批 | `--use-error-organizer-agent --error-batch-size 10` | 不使用 organizer，或 batch size 为 1/20 | CLI 可控 | 修复轮数、LLM 请求数、frontier 推进 |
| Repair 预算 | 64 | 0/8/16/32 | `CGR_RUST_REPAIR_MAX_ITERATIONS` | 成功率和成本曲线 |
| 功能测试修复 | 开启 `RustTestAgent` | 只跑编译修复 | 默认 flags 对比关闭 test | 功能通过率与额外成本 |
| LogAgent | 关闭或默认 | `CGR_USE_LOG_AGENT=1` 或 rtest `--use-log-agent` | 直接运行 | 测试修复轮数、probe 频次 |
| rtest 预算 | 64 | 0/8/16/32 | `CGR_RUST_TEST_MAX_ITERATIONS` | case 修复收益和 timeout |
| rtest prompt budget | 256000 | 64000/128000/512000 | CLI 或环境变量 | 材料不足、重复请求、成本 |
| Pointer/Macro 证据 | 关闭 | `CGR_USE_POINTER_AGENT=1`、`CGR_USE_MACRO_AGENT=1` | 直接运行 | 指针、宏相关错误修复 |
| Frontier 策略 | blocker-aware frontier | naive error-count frontier | 需代码补丁或离线重评分 | 接受退化候选和最终编译率 |
| 回归保护 | 开启 `_check_regression()` | 禁用 baseline pass 检查 | 需代码补丁 | 当前用例通过率与全套通过率差距 |
| 反作弊过滤 | 开启 fake output/stub 过滤 | 禁用过滤 | 需代码补丁 | 硬编码 expected output 和测试污染 |

## 可引用代码证据

| 论点 | 代码证据 |
| --- | --- |
| 默认主线 flags | `scripts/agent.sh:158-168` |
| PointerAgent、MacroAgent 和 LogAgent 使用环境变量开启 | `scripts/agent.sh:171-181` |
| 主入口把默认参数、可选分析参数和用户参数拼接 | `scripts/agent.sh:184-192` |
| rtest 旁路暴露最大修复轮数和 prompt budget | `scripts/rtest_agent.sh:172-173` |
| rtest 旁路暴露 `--use-log-agent` 和 debug probe 上限 | `scripts/rtest_agent.sh:186` |
| 主流程按开关决定是否运行 RustTestAgent | `src/agent/main.py:94-101` |
| 主流程按开关决定是否运行 RustRepairAgent | `src/agent/main.py:216-235` |
| `RustRepairAgent` 记录 `iteration_result`、`accepted_as_best` 和 `accept_reason` | `src/agent/rust_repair_agent.py:4469-4475` |
| `RustTestAgent` 初始化最大修复轮数和 prompt budget | `src/agent/rtest/rust_test_agent.py:88-110` |
| `RustTestAgent` CLI 接收 `--translate-tests` 但当前忽略测试翻译 | `src/agent/rtest/rust_test_agent.py:160`、`src/agent/rtest/rust_test_agent.py:2592` |
| 当前用例通过后执行回归检查 | `src/agent/rtest/rust_test_agent.py:1997` |
| rtest 失败签名用于停滞检测 | `src/agent/rtest/rust_test_agent.py:2077-2082` |

## 实验钩子

- **最小可跑消融。** 优先运行直接由 CLI 或环境变量控制的项：repair 预算、test 预算、ErrorOrganizer、LogAgent、prompt budget、Pointer/Macro 证据。
- **固定上游的 rtest 消融。** 对同一个已编译 Rust crate 多次运行 `scripts/rtest_agent.sh --c-project-path ... --rust-project-path ...`，比较 LogAgent、probe 和 prompt budget。
- **离线 frontier 消融。** 从 `repair_journal.jsonl` 抽取每轮 `result_metrics`，用 naive error-count 策略重放接受决策，估计 blocker-aware frontier 的必要性。
- **成本曲线。** 对 repair/test 预算做阶梯式截断，绘制成功率随 LLM 请求数和耗时增长的曲线。
- **规模分层。** 在小型、中型和大型项目上分别跑消融，避免大型项目成本掩盖小项目趋势。
- **质量约束消融。** 对比不同链路下最终 `unsafe_rate` 和裸指针计数，判断成功率提升是否以更不安全的 Rust 为代价。

## 局限与反例

- 当前仓库没有完整 run manifest，消融结果必须额外记录 git commit、模型、温度、prompt budget、环境变量和输出根。
- 部分关键机制没有 CLI 开关，直接消融需要小补丁。论文中应避免把「未运行」的消融写成实验证据。
- LLM 输出具有随机性，即使温度固定也可能受后端和流式返回影响。关键消融最好重复运行或报告置信区间。
- 默认输出目录容易覆盖历史结果。若未隔离输出，repair journal 和 round logs 会混入不同配置。
- rtest 旁路固定上游时，只能评价功能修复阶段，不能代表端到端成功率。
- 关闭 Spec 或 ContextualRust 后，后续 repair/test 的输入质量会整体改变，不能把失败完全归因于单个阶段。

## 可写入论文位置

- **实验设置：消融矩阵。** 放默认主线与各处理组、控制变量和主要指标。
- **结果章节：阶段贡献。** 分别展示 Spec/ContextualRust、RustRepair、RustTest 和 LogAgent 的收益。
- **分析章节：成本收益。** 用预算曲线说明额外 LLM 轮数带来的边际收益。
- **威胁与局限：消融可比性。** 说明输出隔离、LLM 非确定性和部分机制需要代码钩子的限制。
